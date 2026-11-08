import os, sys
RAIZ = os.path.dirname(os.path.dirname(__file__))
os.environ["MODO_LOCAL"] = "1"
sys.path[:0] = [os.path.join(RAIZ, "common"), os.path.join(RAIZ, "services", "ingesta")]
