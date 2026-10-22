"""
agent.py
--------
Agent ReAct client du serveur MCP Facturation avec Google Gemini.
Le serveur MCP tourne de son côté (port 8000).

Commandes :
  uv run agent.py --outils      # Inventaire des outils, sans LLM
  uv run agent.py --graph       # Exporte le graphe (graphe.png) puis golden set
  uv run agent.py "question"    # Poser une question
  uv run agent.py               # Lancer le golden set
"""
import asyncio
import sys
import time

from langchain_mcp_adapters.client import MultiServerMCPClient

from graph import (
    GOOGLE_API_KEY,
    MCP_URL,
    RECURSION_LIMIT,
    construire_graphe,
    derniere_reponse,
)

GOOGLE_OK = bool(GOOGLE_API_KEY) and len(GOOGLE_API_KEY) > 20


def texte_du_message(msg) -> str:
    """Normalise le contenu d'un message (str ou liste de blocs) en texte."""
    contenu = msg.content
    if isinstance(contenu, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in contenu
        ).strip()
    return str(contenu or "").strip()


def afficher_trace(messages: list) -> dict:
    """Reconstitue la boucle Thought, Action, Observation."""
    outils_utilises, etape = [], 0
    for msg in messages:
        type_msg = msg.__class__.__name__
        if type_msg == "AIMessage":
            pensee = texte_du_message(msg)
            if pensee:
                print(f"  Thought     : {pensee[:200]}")
            for appel in getattr(msg, "tool_calls", []) or []:
                etape += 1
                outils_utilises.append(appel["name"])
                print(f"  Action [{etape}] : {appel['name']}({appel['args']})")
        elif type_msg == "ToolMessage":
            observation = str(msg.content).replace("\n", " ")
            suite = "…" if len(observation) > 220 else ""
            print(f"  Observation : {observation[:220]}{suite}")
    return {"tools_used": outils_utilises, "steps": etape}


async def interroger(agent, question: str, thread_id: str = "default_thread") -> dict:
    print(f"\n{'=' * 72}\nQuestion : {question}\n{'-' * 72}")
    debut = time.time()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }
    try:
        resultat = await agent.ainvoke(
            {"messages": [("user", question)]}, config=config
        )
    except Exception as exc:
        print(f"  ÉCHEC : {exc}")
        return {"answer": "", "tools_used": [], "steps": 0,
                "duration_s": time.time() - debut}

    duree = time.time() - debut
    trace = afficher_trace(resultat["messages"])
    reponse = derniere_reponse(resultat["messages"])
    print(f"{'-' * 72}\nRéponse : {reponse}")
    print(f"[{trace['steps']} appel(s) · {duree:.1f}s · "
          f"outils : {trace['tools_used'] or 'aucun'}]")
    return {"answer": reponse, **trace, "duration_s": duree}


async def main() -> None:
    client = MultiServerMCPClient(
        {"facturation": {"url": MCP_URL, "transport": "streamable_http"}}
    )
    try:
        outils = await client.get_tools()
    except Exception as exc:
        print(f"Serveur MCP injoignable sur {MCP_URL}\n  {type(exc).__name__}: {exc}")
        print("\nCôté serveur, vérifier que server.py tourne :")
        print("  uv run server.py   # streamable-http, port 8000")
        sys.exit(1)

    print(f"{len(outils)} outil(s) découvert(s) sur {MCP_URL} :")
    for outil in outils:
        description = (outil.description or "").splitlines()
        print(f"  - {outil.name:<26} {description[0] if description else ''}")

    if "--outils" in sys.argv:
        return

    if not GOOGLE_OK:
        print("\nClé Google manquante. Renseigner GOOGLE_API_KEY dans le fichier .env")
        sys.exit(1)

    agent = construire_graphe(outils)

    # --- Export du graphe ------------------------------------------------
    if "--graph" in sys.argv or "--graphe" in sys.argv:
        try:
            with open("graphe.png", "wb") as f:
                f.write(agent.get_graph().draw_mermaid_png())
            print("Graphe sauvegardé dans 'graphe.png'")
        except Exception as err:
            print(f"Échec de la génération du PNG : {err}")
            with open("graphe.mmd", "w", encoding="utf-8") as f:
                f.write(agent.get_graph().draw_mermaid())
            print("Syntaxe Mermaid sauvegardée dans 'graphe.mmd'")

    questions = [a for a in sys.argv[1:] if not a.startswith("--")]

    # 1. Questions passées en ligne de commande
    if questions:
        for i, question in enumerate(questions, 1):
            await interroger(agent, question, thread_id=f"cli_thread_{i}")
        return

    # 2. Golden set par défaut
    golden_set = [
        "Quels sont les articles disponibles dans le catalogue ?",
        "Lister tous les clients ayant au moins une facture impayée en retard",
    ]
    print(f"\n{'=' * 72}\nLancement du Golden Set ({len(golden_set)} questions)")
    for i, q in enumerate(golden_set, 1):
        # Un thread_id par question : les mémoires sont isolées
        await interroger(agent, q, thread_id=f"golden_set_thread_{i}")


if __name__ == "__main__":
    asyncio.run(main())