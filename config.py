ORDER_CONFIG = {
    'us-east-2': {
        'range': ['01', '02', '03'],
        'profile': 'prode'
    },
    'us-west-2': {
        'range': [f"{i:02d}" for i in range(4, 21)],
        'profile': 'prodw'
    }
}
DEST_PROFILE = {
    'us-west-2': 'devw'
}
