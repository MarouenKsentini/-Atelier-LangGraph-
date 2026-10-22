"""
graph.py
--------
Construit le graphe LangGraph de l'agent de facturation (Gemini + outils MCP).
"""
import os

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

try:  # la classe n'est pas réexportée par le paquet
    from langchain_google_genai.chat_models import GoogleRateLimitError
except ImportError:  # pragma: no cover - dépend de la version installée
    GoogleRateLimitError = ()  # type: ignore[assignment]

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

load_dotenv()

RECURSION_LIMIT = 15
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-3.8-flash")
# Quota gratuit Gemini = 20 requêtes/jour *par modèle* : on bascule sur un
# modèle de secours quand le principal renvoie 429 RESOURCE_EXHAUSTED.
GOOGLE_MODEL_SECOURS = os.getenv(
    "GOOGLE_MODEL_SECOURS", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite"
)

SYSTEM_PROMPT = """Tu es l'assistant commercial de la société, connecté au système de
facturation.

RÈGLES
- Ne devine jamais un identifiant : utilise l'outil de recherche pour l'obtenir.
- N'invente jamais un prix, un stock, une référence ou un montant.
- Une liste vide signifie « aucun résultat », pas une erreur.
- Réponds en français, de façon concise et chiffrée. Montants en TND HT.

GESTION DU CONTEXTE DE CONVERSATION
- Traite chaque nouvelle question de façon indépendante par défaut.
- Ne relie une question à un échange précédent que si elle contient un indice
  explicite de continuité (« celui-ci », « ce dernier », « et son stock ? »,
  « la même chose pour... », etc.).
- En l'absence d'un tel indice, considère qu'il s'agit d'un nouveau sujet sans
  rapport avec ce qui précède, même si le thème général se ressemble.
"""


class QuotaGeminiEpuise(RuntimeError):
    """Tous les modèles de la chaîne de repli ont échoué (quota la plupart du temps)."""


def derniere_reponse(messages: list) -> str:
    """Retourne le texte du dernier AIMessage non vide."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            contenu = msg.content
            if isinstance(contenu, list):
                morceaux = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in contenu
                ]
                texte = "".join(morceaux).strip()
            else:
                texte = str(contenu or "").strip()
            if texte:
                return texte
    return ""


def sans_additional_properties(schema):
    """Retire récursivement la clé JSON Schema `additionalProperties`.

    Le serveur MCP l'envoie (`false`) mais l'API Gemini ne la gère pas :
    langchain-google-genai la supprime en émettant un warning par outil.
    La retirer ici évite ce bruit sans changer le comportement des outils.
    """
    if isinstance(schema, dict):
        return {
            cle: sans_additional_properties(valeur)
            for cle, valeur in schema.items()
            if cle != "additionalProperties"
        }
    if isinstance(schema, list):
        return [sans_additional_properties(valeur) for valeur in schema]
    return schema


def outils_compatibles_gemini(outils):
    """Copies des outils dont l'args_schema est nettoyé pour Gemini."""
    propres = []
    for outil in outils:
        schema = getattr(outil, "args_schema", None)
        if isinstance(schema, dict):
            outil = outil.model_copy(
                update={"args_schema": sans_additional_properties(schema)}
            )
        propres.append(outil)
    return propres


def construire_graphe(outils):
    outils = outils_compatibles_gemini(outils)
    modeles = [GOOGLE_MODEL]
    modeles += [m.strip() for m in GOOGLE_MODEL_SECOURS.split(",") if m.strip()]
    modeles = list(dict.fromkeys(modeles))  # dédoublonne en gardant l'ordre

    llms = [
        # Pas de `temperature` : les Gemini 3+ ont des réglages d'échantillonnage
        # figés, et la lib émet un UserWarning si on en passe un.
        # `max_retries=1` : sans ça, un 429 (quota) est retenté ~5 fois par modèle
        # avant de basculer, ce qui bloque la console plusieurs minutes.
        ChatGoogleGenerativeAI(
            model=modele,
            google_api_key=GOOGLE_API_KEY,
            max_retries=1,
        ).bind_tools(outils)
        for modele in modeles
    ]
    # 429 / 503 sur le modèle principal -> bascule sur le suivant.
    llm_avec_outils = llms[0].with_fallbacks(llms[1:]) if len(llms) > 1 else llms[0]
    tool_node = ToolNode(outils)

    def appeler_modele(state: MessagesState):
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
        try:
            reponse = llm_avec_outils.invoke(messages)
        except GoogleRateLimitError as exc:
            # LangChain relance l'erreur du *premier* modèle, qui ne dit pas que
            # les suivants ont aussi échoué : on reformule pour être actionnable.
            raise QuotaGeminiEpuise(
                "Quota Gemini épuisé sur tous les modèles essayés "
                f"({', '.join(modeles)}).\n"
                "  Quota gratuit : 20 requêtes/jour/modèle. Attendre la "
                "réinitialisation (minuit Pacifique), changer GOOGLE_MODEL / "
                "GOOGLE_MODEL_SECOURS dans .env, ou activer la facturation Google AI."
            ) from exc
        return {"messages": [reponse]}

    def route_apres_agent(state: MessagesState):
        dernier = state["messages"][-1]
        if getattr(dernier, "tool_calls", None):
            return "tools"
        return END

    graphe = StateGraph(MessagesState)
    graphe.add_node("agent", appeler_modele)
    graphe.add_node("tools", tool_node)
    graphe.add_edge(START, "agent")
    graphe.add_conditional_edges(
        "agent", route_apres_agent, {"tools": "tools", END: END}
    )
    graphe.add_edge("tools", "agent")
    return graphe.compile(checkpointer=InMemorySaver())