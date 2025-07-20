from flask import Flask, request, jsonify
import logging
import json
from concurrent.futures import ThreadPoolExecutor

import swap_rds

app = Flask(__name__, static_folder='static', static_url_path='')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1)


@app.route('/')
def index():
    return app.send_static_file('index.html')


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

    if not order_no or not dest_profile or (not src_profile and not src_profiles):
        return jsonify({'error': 'orderNo and profiles required'}), 400

    logger.info('Starting swap process for orderNo=%s', order_no)
    executor.submit(
        swap_rds.run_swap,
        order_no,
        src_profile,
        dest_profile,
        regions,
        dest_region,
        db_class,
        src_profiles,
    )
    return jsonify({'status': 'started'})

if __name__ == '__main__':
    app.run(debug=True)
