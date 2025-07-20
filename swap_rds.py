import argparse
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import boto3


LOG_FORMAT = '%(asctime)s [%(levelname)s] %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Copy RDS instances by orderNo tag and restore in destination account")
    parser.add_argument('--order-no', required=True, help='Value for the orderNo tag')
    parser.add_argument('--src-profile', required=True, help='AWS CLI profile name for the source account')
    parser.add_argument('--dest-profile', required=True, help='AWS CLI profile name for the destination account')
    parser.add_argument('--regions', nargs='+', default=['us-west-2', 'us-east-2'], help='Source regions to scan')
    parser.add_argument('--dest-region', default='us-west-2', help='Destination region where snapshots will be copied and restored')
    parser.add_argument('--db-class', default='db.t3.micro', help='DB instance class when restoring from snapshot')
    return parser.parse_args()


def get_account_id(session):
    sts = session.client('sts')
    return sts.get_caller_identity()['Account']


def get_rds_instances(session, region, order_no):
    logger.info('Looking for RDS instances with orderNo=%s in %s', order_no, region)
    rds = session.client('rds', region_name=region)
    instances = []
    paginator = rds.get_paginator('describe_db_instances')
    for page in paginator.paginate():
        for inst in page['DBInstances']:
            arn = inst['DBInstanceArn']
            tags = rds.list_tags_for_resource(ResourceName=arn)['TagList']
            for tag in tags:
                if tag['Key'] == 'orderNo' and tag['Value'] == order_no:
                    instances.append(inst)
                    logger.info('Found instance %s in %s', inst['DBInstanceIdentifier'], region)
    return instances


def get_instance_settings(inst):
    """Extract settings from a source RDS instance for restoration."""
    settings = {
        'DBSubnetGroupName': inst.get('DBSubnetGroup', {}).get('DBSubnetGroupName'),
        'VpcSecurityGroupIds': [sg['VpcSecurityGroupId'] for sg in inst.get('VpcSecurityGroups', [])],
        'MultiAZ': inst.get('MultiAZ', False),
        'PubliclyAccessible': inst.get('PubliclyAccessible', False),
        'StorageType': inst.get('StorageType'),
    }
    return settings


def create_snapshot(rds, instance_id):
    snap_id = f'{instance_id}-{int(time.time())}'
    logger.info('Creating snapshot %s', snap_id)
    rds.create_db_snapshot(DBInstanceIdentifier=instance_id, DBSnapshotIdentifier=snap_id)
    waiter = rds.get_waiter('db_snapshot_available')
    waiter.wait(DBSnapshotIdentifier=snap_id)
    logger.info('Snapshot %s is ready', snap_id)
    return snap_id


def share_snapshot(rds, snapshot_id, dest_account_id):
    logger.info('Sharing snapshot %s with %s', snapshot_id, dest_account_id)
    rds.modify_db_snapshot_attribute(
        DBSnapshotIdentifier=snapshot_id,
        AttributeName='restore',
        ValuesToAdd=[dest_account_id]
    )


def copy_snapshot(dest_rds, source_region, snapshot_id, dest_snapshot_id, source_account_id):
    source_arn = f'arn:aws:rds:{source_region}:{source_account_id}:snapshot:{snapshot_id}'
    logger.info('Copying snapshot %s to %s as %s', source_arn, dest_rds.meta.region_name, dest_snapshot_id)
    dest_rds.copy_db_snapshot(
        SourceDBSnapshotIdentifier=source_arn,
        TargetDBSnapshotIdentifier=dest_snapshot_id,
        SourceRegion=source_region
    )
    waiter = dest_rds.get_waiter('db_snapshot_available')
    waiter.wait(DBSnapshotIdentifier=dest_snapshot_id)
    logger.info('Snapshot copy %s is ready', dest_snapshot_id)


def restore_from_snapshot(dest_rds, snapshot_id, instance_id, db_class, order_no, settings):
    logger.info('Restoring instance %s from snapshot %s', instance_id, snapshot_id)
    params = {
        'DBInstanceIdentifier': instance_id,
        'DBSnapshotIdentifier': snapshot_id,
        'DBInstanceClass': db_class,
    }
    if settings.get('DBSubnetGroupName'):
        params['DBSubnetGroupName'] = settings['DBSubnetGroupName']
    if settings.get('VpcSecurityGroupIds'):
        params['VpcSecurityGroupIds'] = settings['VpcSecurityGroupIds']
    if settings.get('MultiAZ') is not None:
        params['MultiAZ'] = settings['MultiAZ']
    if settings.get('PubliclyAccessible') is not None:
        params['PubliclyAccessible'] = settings['PubliclyAccessible']
    if settings.get('StorageType'):
        params['StorageType'] = settings['StorageType']
    dest_rds.restore_db_instance_from_db_snapshot(**params)
    waiter = dest_rds.get_waiter('db_instance_available')
    waiter.wait(DBInstanceIdentifier=instance_id)
    arn = dest_rds.describe_db_instances(DBInstanceIdentifier=instance_id)['DBInstances'][0]['DBInstanceArn']
    dest_rds.add_tags_to_resource(ResourceName=arn, Tags=[{'Key': 'orderNo', 'Value': order_no}])
    logger.info('Restored instance %s is available', instance_id)


def list_instances(order_no, src_profile, regions=None, src_profiles=None):
    """Return identifiers of RDS instances matching ``order_no``.

    Parameters
    ----------
    order_no : str
        Value for the ``orderNo`` tag.
    src_profile : str
        Default profile to use when creating sessions.
    regions : list[str], optional
        Regions to search. Defaults to ``['us-west-2', 'us-east-2']``.
    src_profiles : dict[str, str], optional
        Optional mapping of region to profile to override ``src_profile``.

    Returns
    -------
    list[dict]
        Each dict contains ``region``, ``id`` and ``name`` of an RDS instance.
    """
    if regions is None:
        regions = ['us-west-2', 'us-east-2']

    if src_profiles is None:
        src_profiles = {region: src_profile for region in regions}

    results = []
    for region in regions:
        session = boto3.Session(profile_name=src_profiles.get(region, src_profile))
        instances = get_rds_instances(session, region, order_no)
        for inst in instances:
            results.append({
                'region': region,
                'id': inst['DBInstanceIdentifier'],
                'name': inst.get('DBName')
            })
    return results


def process_region(order_no, src_profile, dest_profile, region, dest_region, db_class, instance_ids=None):
    """Handle the snapshot copy and restore for a single region."""
    src_session = boto3.Session(profile_name=src_profile)
    dest_session = boto3.Session(profile_name=dest_profile)

    src_account_id = get_account_id(src_session)
    dest_account_id = get_account_id(dest_session)

    src_rds = src_session.client('rds', region_name=region)
    dest_rds = dest_session.client('rds', region_name=dest_region)

    if instance_ids is None:
        instances = get_rds_instances(src_session, region, order_no)
    else:
        instances = []
        for iid in instance_ids:
            inst = src_rds.describe_db_instances(DBInstanceIdentifier=iid)['DBInstances'][0]
            instances.append(inst)
    for inst in instances:
        inst_id = inst['DBInstanceIdentifier']
        settings = get_instance_settings(inst)
        snap_id = create_snapshot(src_rds, inst_id)
        share_snapshot(src_rds, snap_id, dest_account_id)
        dest_snap_id = f'copy-{snap_id}'
        copy_snapshot(dest_rds, region, snap_id, dest_snap_id, src_account_id)
        restore_id = f'{inst_id}-copy'
        restore_from_snapshot(dest_rds, dest_snap_id, restore_id, db_class, order_no, settings)


def run_swap(order_no, src_profile, dest_profile, regions=None, dest_region='us-west-2', db_class='db.t3.micro', src_profiles=None, instances=None):
    """Run the snapshot copy and restore process programmatically.

    Parameters
    ----------
    order_no : str
        Value of the orderNo tag to filter instances.
    src_profile : str
        Default source AWS profile used when `src_profiles` is not provided.
    dest_profile : str
        Destination AWS profile.
    regions : list[str], optional
        Source regions to scan. Defaults to ``['us-west-2', 'us-east-2']``.
    dest_region : str, optional
        Region where the snapshots will be restored. Defaults to ``'us-west-2'``.
    db_class : str, optional
        DB instance class when restoring from snapshots.
    src_profiles : dict[str, str], optional
        Mapping of region to source profile. Overrides ``src_profile`` for the
        specified regions.
    instances : list[dict], optional
        Specific instances to process. Each dict must include ``region`` and ``id``.
    """
    if regions is None:
        regions = ['us-west-2', 'us-east-2']

    if src_profiles is None:
        src_profiles = {region: src_profile for region in regions}

    region_instances = defaultdict(list)
    if instances:
        for inst in instances:
            region_instances[inst['region']].append(inst['id'])
        regions = list(region_instances.keys())

    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(
                process_region,
                order_no,
                src_profiles.get(region, src_profile),
                dest_profile,
                region,
                dest_region,
                db_class,
                region_instances.get(region) if instances else None,
            )
            for region in regions
        ]
        for f in futures:
            f.result()


def main():
    args = parse_args()
    run_swap(
        order_no=args.order_no,
        src_profile=args.src_profile,
        dest_profile=args.dest_profile,
        regions=args.regions,
        dest_region=args.dest_region,
        db_class=args.db_class,
    )


if __name__ == '__main__':
    main()
