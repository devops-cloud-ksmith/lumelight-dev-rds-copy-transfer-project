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

## Web UI

You can also launch a small Flask application that exposes the functionality via a REST endpoint and a lightweight React + Bootstrap interface.

### Run the server

```bash
pip install flask boto3
python app.py
```

Then open `http://localhost:5000` in your browser. Fill out the form and submit to start the swap process.

