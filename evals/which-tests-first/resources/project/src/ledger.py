import json

def record(path, row):
    with open(path, 'a') as f:
        f.write(json.dumps(row) + '\n')
