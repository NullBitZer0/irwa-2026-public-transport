from opensearchpy import OpenSearch

client = OpenSearch(hosts=[{"host": "localhost", "port": 9200}])

INDEX_NAME = "transit_routes"

INDEX_MAPPING = {
    "settings": {
        "index": {"knn": True}
    },
    "mappings": {
        "properties": {
            "route_id": {"type": "keyword"},
            "text": {"type": "text"},
            "embedding": {
                "type": "knn_vector",
                "dimension": 384,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "nmslib",
                },
            },
        }
    },
}


def create_index():
    if client.indices.exists(index=INDEX_NAME):
        client.indices.delete(index=INDEX_NAME)
    client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
    print(f"Created index: {INDEX_NAME}")


if __name__ == "__main__":
    create_index()
