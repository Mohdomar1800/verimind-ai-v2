import duckdb
import json

conn = duckdb.connect('data/verimind4.duckdb')
row = conn.execute("SELECT evidence_json FROM analysis_results WHERE analysis_type = 'debug_priority' LIMIT 1").fetchone()
if row:
    print(json.dumps(json.loads(row[0]), indent=2))
else:
    print("No data")
