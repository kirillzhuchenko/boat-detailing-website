import logging

# Logging HTTP actions
logging.basicConfig(level=logging.ERROR, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)