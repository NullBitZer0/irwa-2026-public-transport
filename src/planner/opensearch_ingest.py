import json
from opensearchpy import OpenSearch, helpers
from sentence_transformers import SentenceTransformer

client = OpenSearch(hosts=[{"host": "localhost", "port": 9200}])
embedder = SentenceTransformer("all-MiniLM-L6-v2")
INDEX_NAME = "transit_routes"


def load_schedules():
    with open("data/processed/train_schedules.json") as f:
        trains = json.load(f)
    with open("data/processed/bus_routes.json") as f:
        buses = json.load(f)
    return trains + buses


def ingest():
    schedules = load_schedules()
    texts = [
        f"{s['service_name']} operated by {s['provider']} running from "
        f"{s['origin']} to {s['destination']}. Stops: {', '.join(s.get('stops', []))}."
        for s in schedules
    ]
    embeddings = embedder.encode(texts, normalize_embeddings=True)

    actions = []
    for s, text, emb in zip(schedules, texts, embeddings):
        actions.append({
            "_index": INDEX_NAME,
            "_id": s["route_id"],
            "_source": {
                "route_id": s["route_id"],
                "text": text,
                "embedding": emb.tolist(),
                **s,
            },
        })
    helpers.bulk(client, actions)
    print(f"Ingested {len(actions)} routes into OpenSearch")


if __name__ == "__main__":
    ingest()