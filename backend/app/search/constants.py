"""Search-layer constants shared by the API contract (schemas.py) and the
service layer (service.py), so neither has to import the other for them.
"""

# Hard upper bound on top_k: a caller-supplied value is clamped, never trusted,
# so search_chunks can't be made to sort/return an unbounded result set.
MAX_TOP_K = 50

DEFAULT_TOP_K = 5
