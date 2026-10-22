"""
chat.py
-------
Boucle conversationnelle avec l'agent Gemini : la mémoire est conservée
d'un tour à l'autre par le checkpointer (un thread_id par session).

uv run chat.py   (quitter : 'quit', 'exit', 'q' ou Ctrl-C)
"""
import asyncio
import sys
import uuid

from langchain_mcp_adapters.client import MultiServerMCPClient

from graph import (
    GOOGLE_API_KEY,
    MCP_URL,
    RECURSION_LIMIT,
    construire_graphe,
    derniere_reponse,
)


async def main() -> None:
    if not GOOGLE_API_KEY:
        print("Clé Gemini manquante. Renseigner GOOGLE_API_KEY dans .env")
        sys.exit(1)

    client = MultiServerMCPClient(
        {"facturation": {"url": MCP_URL, "transport": "streamable_http"}}
    )
    try:
        outils = await client.get_tools()
    except Exception as exc:
        print(f"Serveur MCP injoignable sur {MCP_URL} — {exc}")
        sys.exit(1)

    agent = construire_graphe(outils)
    config = {
        "configurable": {"thread_id": str(uuid.uuid4())},
        "recursion_limit": RECURSION_LIMIT,
    }

    print(f"Assistant facturation (Gemini) — {len(outils)} outils · {MCP_URL}")
    print("Tapez 'quit' pour sortir.\n")

    while True:
        try:
            question = input("vous > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"quit", "exit", "q"}:
            break

        try:
            # Seule la nouvelle question est envoyée : le checkpointer
            # réinjecte l'historique du thread.
            resultat = await agent.ainvoke(
                {"messages": [("user", question)]}, config=config
            )
        except Exception as exc:
            print(f"[erreur] {exc}\n")
            continue

        print(f"\nagent > {derniere_reponse(resultat['messages'])}\n")


if __name__ == "__main__":
    asyncio.run(main())