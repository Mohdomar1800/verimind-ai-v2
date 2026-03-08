import duckdb
import json

conn = duckdb.connect('data/verimind4.duckdb')
row = conn.execute("SELECT repo, module_name, context_score, risk_level, evidence_json FROM verification_context LIMIT 1").fetchone()
if row:
    print(f"Repo: {row[0]}")
    print(f"Module: {row[1]}")
    print(f"Score: {row[2]}")
    print(f"Risk: {row[3]}")
    print(f"Evidence: {json.dumps(json.loads(row[4]), indent=2)}")
else:
    print("No data")
