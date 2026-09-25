import json, os

def get(path):
    try:
        return json.load(open(path))
    except OSError:
        return None

def put(path, obj):
    json.dump(obj, open(path, 'w'))
