import duckdb
import json

conn = duckdb.connect('data/verimind4.duckdb')

print("--- Debug Priority Count ---")
try:
    c = conn.execute("SELECT COUNT(*) FROM analysis_results WHERE analysis_type = 'debug_priority'").fetchone()[0]
    print(f"Total Debug Items: {c}")
except:
    print("No analysis_results table or error")

print("\n--- Top 10 Debug Scores ---")
try:
    items = conn.execute("SELECT item_id, score, rank FROM analysis_results WHERE analysis_type = 'debug_priority' ORDER BY rank ASC LIMIT 10").fetchall()
    for item in items:
        print(f"ID: {item[0]}, Score: {item[1]}, Rank: {item[2]}")
except:
    print("Error fetching items")

print("\n--- Statistics for Verification Context ---")
try:
    ctx = conn.execute("SELECT COUNT(*), AVG(context_score) FROM verification_context").fetchone()
    print(f"Total Modules: {ctx[0]}, Average Context Score: {ctx[1]}")
except:
    print("Error fetching context stats")
