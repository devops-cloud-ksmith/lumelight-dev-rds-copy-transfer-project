import logging
import time
from typing import List

import boto3

LOG_FORMAT = '%(asctime)s [%(levelname)s] %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


def get_account_id(session):
    """Return the AWS account ID for the provided session."""
    sts = session.client('sts')
    return sts.get_caller_identity()['Account']


# VPC migration helpers -------------------------------------------------------

def find_vpcs(session, region: str, order_no: str) -> List[dict]:
    """Locate VPCs tagged with ``orderNo`` in ``region``."""
    ec2 = session.client('ec2', region_name=region)
    resp = ec2.describe_vpcs(Filters=[{'Name': 'tag:orderNo', 'Values': [order_no]}])
    return resp.get('Vpcs', [])


def replicate_vpc(src_session, dest_session, region: str, vpc: dict) -> str:
    """Create a new VPC in ``dest_session`` replicating CIDR and tags."""
    ec2_src = src_session.client('ec2', region_name=region)
    ec2_dest = dest_session.client('ec2', region_name=region)

    cidr = vpc['CidrBlock']
    tags = [t for t in vpc.get('Tags', [])]

    result = ec2_dest.create_vpc(CidrBlock=cidr)
    vpc_id = result['Vpc']['VpcId']
    if tags:
        ec2_dest.create_tags(Resources=[vpc_id], Tags=tags)

    logger.info('Created VPC %s in %s', vpc_id, region)
    
    # replicate subnets (CIDR blocks only)
    subnets = ec2_src.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc['VpcId']]}]).get('Subnets', [])
    for subnet in subnets:
        resp = ec2_dest.create_subnet(VpcId=vpc_id, CidrBlock=subnet['CidrBlock'])
        subnet_id = resp['Subnet']['SubnetId']
        if subnet.get('Tags'):
            ec2_dest.create_tags(Resources=[subnet_id], Tags=subnet['Tags'])
        logger.info('Created subnet %s', subnet_id)

    return vpc_id


# EC2 migration helpers -------------------------------------------------------

def find_instances(session, region: str, order_no: str) -> List[dict]:
    """Locate EC2 instances tagged with ``orderNo`` in ``region``."""
    ec2 = session.client('ec2', region_name=region)
    resp = ec2.describe_instances(Filters=[{'Name': 'tag:orderNo', 'Values': [order_no]}])
    instances = []
    for res in resp['Reservations']:
        instances.extend(res['Instances'])
    return instances


def create_image(ec2, instance_id: str) -> str:
    ami_name = f'migrate-{instance_id}-{int(time.time())}'
    resp = ec2.create_image(InstanceId=instance_id, Name=ami_name, NoReboot=True)
    ami_id = resp['ImageId']
    waiter = ec2.get_waiter('image_available')
    waiter.wait(ImageIds=[ami_id])
    logger.info('Created AMI %s from %s', ami_id, instance_id)
    return ami_id


def share_image(ec2, ami_id: str, dest_account_id: str):
    logger.info('Sharing AMI %s with %s', ami_id, dest_account_id)
    ec2.modify_image_attribute(ImageId=ami_id, Attribute='launchPermission', OperationType='add', UserIds=[dest_account_id])


def copy_image(dest_ec2, source_region: str, ami_id: str) -> str:
    result = dest_ec2.copy_image(SourceImageId=ami_id, SourceRegion=source_region, Name=f'copy-{ami_id}')
    dest_ami_id = result['ImageId']
    waiter = dest_ec2.get_waiter('image_available')
    waiter.wait(ImageIds=[dest_ami_id])
    logger.info('Copied AMI to destination: %s', dest_ami_id)
    return dest_ami_id


def launch_instance(dest_ec2, ami_id: str, instance_type: str = 't3.micro') -> str:
    resp = dest_ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1, InstanceType=instance_type)
    instance_id = resp['Instances'][0]['InstanceId']
    logger.info('Launched instance %s', instance_id)
    return instance_id


# Orchestration ---------------------------------------------------------------

def migrate_resources(order_no: str, src_profile: str, dest_profile: str, region: str = 'us-west-2', instance_type: str = 't3.micro'):
    """Migrate VPCs and EC2 instances tagged with ``order_no`` from ``src_profile`` to ``dest_profile`` in ``region``."""
    src_session = boto3.Session(profile_name=src_profile)
    dest_session = boto3.Session(profile_name=dest_profile)
    dest_account = get_account_id(dest_session)

    # VPCs
    vpcs = find_vpcs(src_session, region, order_no)
    for vpc in vpcs:
        replicate_vpc(src_session, dest_session, region, vpc)

    # EC2 instances
    instances = find_instances(src_session, region, order_no)
    ec2_src = src_session.client('ec2', region_name=region)
    ec2_dest = dest_session.client('ec2', region_name=region)
    for inst in instances:
        iid = inst['InstanceId']
        ami_id = create_image(ec2_src, iid)
        share_image(ec2_src, ami_id, dest_account)
        dest_ami = copy_image(ec2_dest, region, ami_id)
        launch_instance(ec2_dest, dest_ami, instance_type=instance_type)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Migrate VPCs and EC2 instances by orderNo tag')
    parser.add_argument('--order-no', required=True)
    parser.add_argument('--src-profile', required=True)
    parser.add_argument('--dest-profile', required=True)
    parser.add_argument('--region', default='us-west-2')
    parser.add_argument('--instance-type', default='t3.micro')
    args = parser.parse_args()

    migrate_resources(args.order_no, args.src_profile, args.dest_profile, args.region, args.instance_type)
