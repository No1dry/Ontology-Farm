"""Semantic process initialization: never toggle normalization per request."""
import rdflib

# All parser/query consumers import this package before constructing RDF terms.
rdflib.NORMALIZE_LITERALS = False
