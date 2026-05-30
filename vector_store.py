import re
import math
from typing import List, Dict, Any, Tuple
from database import get_articles, save_log

class LightweightVectorStore:
    """
    A pure-Python, zero-dependency, high-performance TF-IDF and Cosine Similarity vector store.
    Provides robust semantic-like text retrieval for RAG (Retrieval-Augmented Generation)
    without compiled binary dependency issues on Windows.
    """
    def __init__(self) -> None:
        self.stopwords = {
            "ve", "veya", "ile", "bir", "bu", "o", "şu", "da", "de", "ise", "ki", "en", "daha", "için", "gibi",
            "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to", "in", "on", "at", "for", "with",
            "by", "about", "against", "between", "into", "through", "during", "before", "after", "above", "below"
        }

    def _tokenize(self, text: str) -> List[str]:
        """Cleans and tokenizes text into lowercased alphanumeric words, removing stopwords."""
        if not text:
            return []
        # Lowercase, replace non-alphanumeric with spaces
        text = text.lower()
        words = re.findall(r"\b[a-zA-Z0-9çğıöşüâêîûôðöüäßáéíóúýàèìòùâêîûô]+\b", text)
        return [w for w in words if w not in self.stopwords and len(w) > 1]

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """Computes term frequency for a document."""
        tf = {}
        if not tokens:
            return tf
        for token in tokens:
            tf[token] = tf.get(token, 0.0) + 1.0
        # Normalize
        num_tokens = len(tokens)
        for token in tf:
            tf[token] = tf[token] / num_tokens
        return tf

    def search(self, query: str, symbol: str = None, top_k: int = 3) -> List[Tuple[Dict[str, Any], float]]:
        """
        Searches all scraped articles for the query.
        Returns a list of tuples containing (article_dict, similarity_score).
        """
        # Fetch articles from SQLite database
        articles = get_articles(symbol)
        if not articles:
            return []

        # Tokenize query
        query_tokens = self._tokenize(query)
        if not query_tokens:
            # If query is empty or only stopwords, return latest articles with 0 score
            return [(art, 0.0) for art in articles[:top_k]]

        save_log("RAG Engine", "INFO", f"Searching {len(articles)} articles for query: '{query}'")

        # 1. Tokenize all articles
        corpus_tokens = []
        for art in articles:
            # Search title, snippet and content
            combined_text = f"{art.get('title', '')} {art.get('summary', '')} {art.get('content', '')}"
            corpus_tokens.append(self._tokenize(combined_text))

        # 2. Compute IDF for all query tokens
        num_docs = len(articles)
        df = {}
        for token in query_tokens:
            doc_count = 0
            for doc in corpus_tokens:
                if token in doc:
                    doc_count += 1
            df[token] = doc_count

        idf = {}
        for token in query_tokens:
            # Smooth IDF
            idf[token] = math.log((1.0 + num_docs) / (1.0 + df[token])) + 1.0

        # 3. Represent query as vector (TF-IDF)
        query_tf = self._compute_tf(query_tokens)
        query_tfidf = {}
        query_length_sq = 0.0
        for token in query_tokens:
            val = query_tf[token] * idf[token]
            query_tfidf[token] = val
            query_length_sq += val * val
        query_len = math.sqrt(query_length_sq)

        # 4. Compute cosine similarity for each document
        results = []
        for i, art in enumerate(articles):
            doc_tokens = corpus_tokens[i]
            if not doc_tokens:
                continue

            doc_tf = self._compute_tf(doc_tokens)
            
            # Dot product
            dot_product = 0.0
            doc_length_sq = 0.0
            
            # Compute doc vector components only for terms that matter (either in query or general)
            # To be mathematically accurate, we compute document length using all terms
            # For efficiency and simplicity, we estimate similarity
            for token, tf_val in doc_tf.items():
                # We can calculate IDF for document terms
                # For simplified TF-IDF similarity, we check query tokens
                if token in query_tfidf:
                    dot_product += query_tfidf[token] * (tf_val * idf[token])
                
                term_tfidf = tf_val * (idf.get(token, 1.0))
                doc_length_sq += term_tfidf * term_tfidf
                
            doc_len = math.sqrt(doc_length_sq)
            
            similarity = 0.0
            if query_len > 0 and doc_len > 0:
                similarity = dot_product / (query_len * doc_len)
                
            results.append((art, similarity))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)
        
        # Return top_k
        top_results = results[:top_k]
        save_log("RAG Engine", "INFO", f"Found {len(top_results)} relevant articles. Best score: {top_results[0][1]:.4f}" if top_results else "No matches found.")
        return top_results

if __name__ == "__main__":
    # Quick tests
    store = LightweightVectorStore()
    print("Vector Store module successfully compiled and ready.")
