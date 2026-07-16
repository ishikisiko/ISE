from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional, Union

from langchain_core.documents import Document as LCDocument
from utils.embedding_config import resolve_embedding_settings


@dataclass
class Document:
    """Simple text container with an optional source label."""

    content: str
    source: Optional[str] = None
    
    @classmethod
    def from_langchain(cls, lc_doc: LCDocument) -> "Document":
        """Convert a LangChain Document to our Document format."""
        return cls(
            content=lc_doc.page_content,
            source=lc_doc.metadata.get("source")
        )
    
    def to_langchain(self) -> LCDocument:
        """Convert to LangChain Document format."""
        return LCDocument(
            page_content=self.content,
            metadata={"source": self.source}
        )


class LangChainFileReader:
    """Document loader using LangChain loaders for .txt, .md, and .pdf files.
    
    This is the new LangChain-based implementation that replaces the custom FileReader.
    """
    
    # Mapping of file extensions to LangChain loaders
    LOADER_MAPPING = {
        ".pdf": ("langchain_community.document_loaders", "PyPDFLoader", {}),
        ".txt": ("langchain_community.document_loaders", "TextLoader", {"encoding": "utf-8"}),
        ".md": ("langchain_community.document_loaders", "UnstructuredMarkdownLoader", {}),
    }
    
    def __init__(self, path: str, recursive: bool = True) -> None:
        if not os.path.isdir(path):
            raise ValueError(f"Path '{path}' is not a valid directory.")
        self.path = path
        self.recursive = recursive

    def load(self) -> List[Document]:
        """Load all supported documents from the configured path."""
        documents = []
        
        # Walk through directory
        for root, _, files in os.walk(self.path):
            if not self.recursive and root != self.path:
                continue
            
            for file in files:
                file_path = os.path.join(root, file)
                ext = os.path.splitext(file)[1].lower()
                
                if ext in self.LOADER_MAPPING:
                    try:
                        module_name, class_name, loader_kwargs = self.LOADER_MAPPING[ext]
                        module = __import__(module_name, fromlist=[class_name])
                        loader_cls = getattr(module, class_name)
                        loader = loader_cls(file_path, **loader_kwargs)
                        lc_docs = loader.load()
                        
                        for lc_doc in lc_docs:
                            documents.append(Document.from_langchain(lc_doc))
                            
                    except Exception as exc:
                        print(f"Skipping file '{file_path}': {exc}")
                        continue
        
        return documents
    
    def load_as_langchain_docs(self) -> List[LCDocument]:
        """Load documents directly as LangChain Documents."""
        documents = []
        
        for root, _, files in os.walk(self.path):
            if not self.recursive and root != self.path:
                continue
            
            for file in files:
                file_path = os.path.join(root, file)
                ext = os.path.splitext(file)[1].lower()
                
                if ext in self.LOADER_MAPPING:
                    try:
                        module_name, class_name, loader_kwargs = self.LOADER_MAPPING[ext]
                        module = __import__(module_name, fromlist=[class_name])
                        loader_cls = getattr(module, class_name)
                        loader = loader_cls(file_path, **loader_kwargs)
                        documents.extend(loader.load())
                    except Exception as exc:
                        print(f"Skipping file '{file_path}': {exc}")
                        continue
        
        return documents


# Alias for backward compatibility
FileReader = LangChainFileReader


class LangChainVectorStore:
    """FAISS-based vector store backed by LangChain primitives.
    
    This class provides a unified interface for document indexing and retrieval
    using a FAISS vector store with the configured embedding provider.
    """

    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        settings = resolve_embedding_settings(config, model_name=model_name)
        self.model_name = str(settings["model"])
        self.embedding_provider = str(settings["provider"])
        self.embedding_base_url = settings["base_url"]
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        if self.embedding_provider == "openai_compatible":
            if not settings["api_key"]:
                raise ValueError(
                    "Embedding provider 'openai_compatible' requires 'embeddings.api_key' "
                    "in config.json or EMBEDDING_API_KEY / OPENAI_API_KEY in the environment."
                )
            if not settings["base_url"]:
                raise ValueError(
                    "Embedding provider 'openai_compatible' requires a real 'embeddings.base_url' "
                    "in config.json or EMBEDDING_BASE_URL in the environment."
                )
            from langchain_openai import OpenAIEmbeddings

            self._embedder = OpenAIEmbeddings(
                model=self.model_name,
                base_url=settings["base_url"],
                api_key=settings["api_key"],
                timeout=settings["timeout"],
                chunk_size=10,
                tiktoken_enabled=False,
                check_embedding_ctx_length=False,
            )
        elif self.embedding_provider == "huggingface":
            from langchain_huggingface import HuggingFaceEmbeddings

            self._embedder = HuggingFaceEmbeddings(model_name=self.model_name)
        else:
            raise ValueError(
                f"Unsupported embedding provider '{self.embedding_provider}'. "
                "Expected 'huggingface' or 'openai_compatible'."
            )
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        self._store: Optional[Any] = None

    def index(self, documents: Union[List[Document], List[LCDocument]]) -> int:
        """Index a list of documents and return the number of chunks stored.
        
        Accepts both our Document format and LangChain Document format.
        """
        # Convert to LangChain Documents if needed
        lc_docs = []
        for doc in documents:
            if isinstance(doc, LCDocument):
                if doc.page_content:
                    lc_docs.append(doc)
            elif isinstance(doc, Document):
                if doc.content:
                    lc_docs.append(doc.to_langchain())
        
        split_docs = self._splitter.split_documents(lc_docs)
        if not split_docs:
            self._store = None
            return 0

        from langchain_community.vectorstores import FAISS

        self._store = FAISS.from_documents(split_docs, self._embedder)
        return len(split_docs)

    def index_from_directory(self, path: str, recursive: bool = True) -> int:
        """Load and index documents from a directory.
        
        Convenience method that combines loading and indexing.
        """
        reader = LangChainFileReader(path, recursive=recursive)
        documents = reader.load()
        return self.index(documents)

    def search(self, query: str, *, k: int = 5) -> List[Document]:
        """Search for the top k most similar documents to a query."""
        if not self._store:
            return []
        hits = self._store.similarity_search(query, k=k)
        return [
            Document(content=hit.page_content, source=hit.metadata.get("source"))
            for hit in hits
        ]
    
    def search_with_scores(self, query: str, *, k: int = 5) -> List[tuple[Document, float]]:
        """Search for documents with relevance scores."""
        if not self._store:
            return []
        hits = self._store.similarity_search_with_score(query, k=k)
        return [
            (Document(content=doc.page_content, source=doc.metadata.get("source")), score)
            for doc, score in hits
        ]
    
    def as_retriever(self, **kwargs):
        """Return a LangChain retriever for use in LCEL chains."""
        if not self._store:
            raise ValueError("Vector store is not initialized. Call index() first.")
        return self._store.as_retriever(**kwargs)

    @property
    def is_ready(self) -> bool:
        return self._store is not None
    
    def save_local(self, folder_path: str) -> None:
        """Save the vector store to disk."""
        if self._store:
            self._store.save_local(folder_path)
    
    @classmethod
    def load_local(
        cls,
        folder_path: str,
        *,
        model_name: Optional[str] = None,
        config: Optional[dict[str, Any]] = None,
        allow_dangerous_deserialization: bool = True,
    ) -> "LangChainVectorStore":
        """Load a vector store from disk."""
        instance = cls(model_name=model_name, config=config)
        from langchain_community.vectorstores import FAISS

        instance._store = FAISS.load_local(
            folder_path,
            instance._embedder,
            allow_dangerous_deserialization=allow_dangerous_deserialization,
        )
        return instance
