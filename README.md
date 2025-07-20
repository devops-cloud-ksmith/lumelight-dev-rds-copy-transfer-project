# lumelight-dev-rds-copy-transfer-project

This repository provides a Python script that automates copying RDS instances from a source AWS account to a destination account using snapshots. Instances are selected based on the `orderNo` tag. Snapshots are copied across accounts and restored in the destination region.

## Requirements
- Python 3 with `boto3` installed
- AWS CLI profiles configured for the source and destination accounts

## Usage
```bash
python swap_rds.py \
    --order-no <ORDER_NO> \
    --src-profile <source-profile> \
    --dest-profile <destination-profile> \
    --regions us-west-2 us-east-2 \
    --dest-region us-west-2
```

The script will:
1. Find RDS instances in the source account with the specified `orderNo` tag.
2. Create snapshots without rebooting the source instances.
3. Share and copy the snapshots to the destination account.
4. Restore new RDS instances in the destination region from those snapshots.

Restored instances will have the `orderNo` tag applied. The process runs concurrently for all specified regions to speed up execution.
Important settings such as subnet groups, security groups and network accessibility are copied from the source instance so the replacement behaves the same in the target account.

When using the web interface you can select AWS CLI profiles from predefined dropdowns:

- **Source us-west-2** – profile `prodw`
- **Source us-east-2** – profile `prode`
- **Destination us-west-2** – profile `devw`

The `orderNo` dropdown is generated from a configuration mapping:

- `us-east-2` &ndash; values `01`&ndash;`03`
- `us-west-2` &ndash; values `04`&ndash;`20`

The web page allows you to list resources for a chosen order number and select which specific instances to swap.

### List resources

Send a GET request to `/resources` with `orderNo`, `regions`, and `srcProfiles` parameters to retrieve matching RDS instances. Each entry in the response includes the region, instance identifier and DB name. The web UI displays checkboxes so you can choose exactly which sources to snapshot and swap.

## Web UI

You can also launch a small Flask application that exposes the functionality via a REST endpoint and a lightweight React + Bootstrap interface.

### Run the server

```bash
pip install flask boto3
python app.py
```

Then open `http://localhost:5000` in your browser. Fill out the form and submit to start the swap process.

## VPC and EC2 Migration

The repository also includes `migrate.py` for copying VPCs and EC2 instances between accounts using the same `orderNo` tag.

```bash
python migrate.py \
    --order-no <ORDER_NO> \
    --src-profile <source-profile> \
    --dest-profile <destination-profile> \
    --region us-west-2
```

This creates matching VPCs and AMIs in the destination account and launches new instances from those images. The web interface exposes a "Migrate VPC & EC2" tab for triggering the same workflow.

