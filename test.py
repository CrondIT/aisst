from rag_chain import get_giga_embeddings
from rag_chain import check_vector_db
from global_state import GUEST_RAG_DIR

embeddings = get_giga_embeddings('Embeddings')
db = check_vector_db(GUEST_RAG_DIR, embeddings)

# Берём несколько документов и смотрим их метаданные
results = db.get(limit=5, include=['metadatas'])
for i, meta in enumerate(results['metadatas']):
    print(f'[{i}] {meta}')
