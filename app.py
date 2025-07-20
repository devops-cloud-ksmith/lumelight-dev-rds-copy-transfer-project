from flask import Flask, request, jsonify
import logging
from concurrent.futures import ThreadPoolExecutor

import swap_rds

app = Flask(__name__, static_folder='static', static_url_path='')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1)


@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/swap', methods=['POST'])
def swap_endpoint():
    data = request.get_json(force=True)
    order_no = data.get('orderNo')
    src_profile = data.get('srcProfile')
    dest_profile = data.get('destProfile')
    regions = data.get('regions') or ['us-west-2', 'us-east-2']
    dest_region = data.get('destRegion', 'us-west-2')
    db_class = data.get('dbClass', 'db.t3.micro')

    if not order_no or not src_profile or not dest_profile:
        return jsonify({'error': 'orderNo, srcProfile and destProfile required'}), 400

    logger.info('Starting swap process for orderNo=%s', order_no)
    executor.submit(
        swap_rds.run_swap,
        order_no,
        src_profile,
        dest_profile,
        regions,
        dest_region,
        db_class,
    )
    return jsonify({'status': 'started'})

if __name__ == '__main__':
    app.run(debug=True)
