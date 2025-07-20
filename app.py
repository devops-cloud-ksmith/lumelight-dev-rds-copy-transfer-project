from flask import Flask, request, jsonify
import logging
import json
from concurrent.futures import ThreadPoolExecutor

import swap_rds
import migrate

app = Flask(__name__, static_folder='static', static_url_path='')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1)
progress_log: list[str] = []


@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/progress')
def progress():
    """Return progress log of the most recent operation."""
    return jsonify(progress_log)


@app.route('/resources')
def resources():
    """Return RDS instances for a given orderNo."""
    order_no = request.args.get('orderNo')
    src_profile = request.args.get('srcProfile')
    src_profiles_param = request.args.get('srcProfiles')
    regions_param = request.args.get('regions')

    if regions_param:
        regions = regions_param.split()
    else:
        regions = ['us-west-2', 'us-east-2']

    src_profiles = None
    if src_profiles_param:
        try:
            src_profiles = json.loads(src_profiles_param)
        except Exception:
            return jsonify({'error': 'invalid srcProfiles'}), 400

    if not order_no or (not src_profile and not src_profiles):
        return jsonify({'error': 'orderNo and profiles required'}), 400

    instances = swap_rds.list_instances(order_no, src_profile, regions, src_profiles)
    return jsonify(instances)

@app.route('/swap', methods=['POST'])
def swap_endpoint():
    data = request.get_json(force=True)
    order_no = data.get('orderNo')
    src_profile = data.get('srcProfile')
    src_profiles = data.get('srcProfiles')  # optional mapping region->profile
    dest_profile = data.get('destProfile')
    regions = data.get('regions') or ['us-west-2', 'us-east-2']
    dest_region = data.get('destRegion', 'us-west-2')
    db_class = data.get('dbClass', 'db.t3.micro')
    instances = data.get('instances')

    if not order_no or not dest_profile or (not src_profile and not src_profiles):
        return jsonify({'error': 'orderNo and profiles required'}), 400

    logger.info('Starting swap process for orderNo=%s', order_no)

    progress_log.clear()

    def progress_cb(msg):
        progress_log.append(msg)
        logger.info(msg)

    executor.submit(
        swap_rds.run_swap,
        order_no,
        src_profile,
        dest_profile,
        regions,
        dest_region,
        db_class,
        src_profiles,
        instances,
        progress_cb,
    )
    return jsonify({'status': 'started'})


@app.route('/migrate', methods=['POST'])
def migrate_endpoint():
    """Migrate VPCs and EC2 instances based on orderNo."""
    data = request.get_json(force=True)
    order_no = data.get('orderNo')
    src_profile = data.get('srcProfile')
    dest_profile = data.get('destProfile')
    region = data.get('region', 'us-west-2')
    instance_type = data.get('instanceType', 't3.micro')

    if not order_no or not src_profile or not dest_profile:
        return jsonify({'error': 'orderNo, srcProfile and destProfile required'}), 400

    logger.info('Starting migration orderNo=%s', order_no)
    executor.submit(migrate.migrate_resources, order_no, src_profile, dest_profile, region, instance_type)
    return jsonify({'status': 'started'})

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5001)
