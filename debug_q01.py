from src.planner.hybrid_retriever import HybridTransitRetriever
import json

retriever = HybridTransitRetriever()

with open("evaluation/benchmark_queries.json") as f:
    tests = json.load(f)

q01 = next(t for t in tests if t["id"] == "Q01")
print("Query:", q01["query"])
print("Expected ground_truth_id:", q01["ground_truth_id"])

results = retriever.retrieve_candidates(query=q01["query"], top_k=5)
print("\nActual top 5 returned:")
for r in results:
    print(f"  {r['route_id']} | {r['service_name']} | score={r['rrf_score']}")


with open("data/processed/train_schedules.json") as f:
    trains = json.load(f)

match = next((t for t in trains if t["route_id"] == "TRAIN-1002"), None)
print("\n--- TRAIN-1002 raw data ---")
print(json.dumps(match, indent=2) if match else "TRAIN-1002 NOT FOUND in train_schedules.json")