"""
CLI entry point: tkg
Commands: index, search, outline, callers, callees, routes, tables, context, stats
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from .biz_query_engine import BizQueryEngine
from .buyer_intelligence import BuyerIntelligenceEngine
from .company_intelligence import CompanyIntelligenceEngine
from .competitive_intelligence import CompetitiveIntelligenceEngine
from .db import GraphDB
from .executive_decision import ExecutiveDecisionEngine
from .importers import CSVImporter, JSONImporter, TenderScopeImporter
from .indexer import Indexer
from .opportunity_intelligence import OpportunityIntelligenceEngine
from .query_engine import QueryEngine
from .relationship_intelligence import RelationshipIntelligenceEngine
from .repository import open_repository

console = Console()


def _get_engine(repo: str) -> tuple[GraphDB, QueryEngine]:
    repo_path = Path(repo).resolve()
    db_path = repo_path / ".tkg" / "graph.db"
    db = GraphDB(db_path)
    db.connect()
    return db, QueryEngine(db)


@click.group()
def main() -> None:
    """TenderScope Knowledge Graph — local repo indexer and query engine."""


# ── tkg index ─────────────────────────────────────────────────────────────────


@main.command()
@click.argument("repo", default=".", type=click.Path(exists=True))
@click.option("--full", is_flag=True, help="Force full re-index (clear incremental cache)")
def index(repo: str, full: bool) -> None:
    """Index a repository into the knowledge graph."""
    repo_path = Path(repo).resolve()
    db_path = repo_path / ".tkg" / "graph.db"
    db = GraphDB(db_path)
    db.connect()

    total_files = [0]

    def progress(fp: str, done: int, total: int) -> None:
        if done == 1 or done % 50 == 0 or done == total:
            console.print(f"[dim]{done}/{total}[/dim] {fp}", highlight=False)
        total_files[0] = total

    with console.status(f"[bold green]Indexing {repo_path}..."):
        indexer = Indexer(db, str(repo_path), progress_cb=progress, incremental=not full)
        stats = indexer.run()

    t = Table(title="Index Complete", show_header=True)
    t.add_column("Metric", style="cyan")
    t.add_column("Value", justify="right")
    t.add_row("Entities", str(stats.get("entities", 0)))
    t.add_row("Relations", str(stats.get("relations", 0)))
    t.add_row("Files scanned", str(stats.get("files_scanned", 0)))
    t.add_row("New entities", str(stats.get("new_entities", 0)))
    t.add_row("Resolved relations", str(stats.get("resolved_relations", 0)))
    t.add_row("Elapsed", f"{stats.get('elapsed_s', 0)}s")
    for lang, count in (stats.get("languages") or {}).items():
        t.add_row(f"  {lang} files", str(count))
    console.print(t)
    db.close()


# ── tkg search ────────────────────────────────────────────────────────────────


@main.command()
@click.argument("query")
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--kind", multiple=True, help="Entity kind filter (can repeat)")
@click.option("--limit", default=20)
@click.option("--json", "as_json", is_flag=True)
def search(query: str, repo: str, kind: tuple, limit: int, as_json: bool) -> None:
    """Search entities by name or keyword."""
    db, engine = _get_engine(repo)
    result = engine.search(query, list(kind) or None, limit)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        t = Table(title=f"Search: {query} ({result['count']} results)")
        t.add_column("Kind", style="cyan", width=14)
        t.add_column("Name", style="bold")
        t.add_column("File:Line", style="dim")
        t.add_column("Signature", style="dim", max_width=50)
        for r in result["results"]:
            t.add_row(
                r["kind"],
                r["qualified_name"],
                f"{r['file']}:{r['line']}",
                r.get("signature", ""),
            )
        console.print(t)
    db.close()


# ── tkg outline ───────────────────────────────────────────────────────────────


@main.command()
@click.argument("file_path")
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True)
def outline(file_path: str, repo: str, as_json: bool) -> None:
    """Show all entities defined in a file."""
    db, engine = _get_engine(repo)
    result = engine.get_file_outline(file_path)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
        else:
            for fblock in result.get("files", []):
                console.print(f"\n[bold]{fblock['file']}[/bold]")
                t = Table(show_header=True)
                t.add_column("Line", style="dim", justify="right", width=6)
                t.add_column("Kind", style="cyan", width=14)
                t.add_column("Name", style="bold")
                t.add_column("Signature", style="dim", max_width=60)
                for e in fblock["entities"]:
                    t.add_row(
                        str(e["line"]),
                        e["kind"],
                        e["name"],
                        e.get("signature", ""),
                    )
                console.print(t)
    db.close()


# ── tkg callers ───────────────────────────────────────────────────────────────


@main.command()
@click.argument("qualified_name")
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--depth", default=1)
@click.option("--json", "as_json", is_flag=True)
def callers(qualified_name: str, repo: str, depth: int, as_json: bool) -> None:
    """Find all callers of a function."""
    db, engine = _get_engine(repo)
    result = engine.get_callers(qualified_name, depth)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
        else:
            console.print(f"[bold]Callers of:[/bold] {result['target']['qualified_name']}")
            for c in result["callers"]:
                e = c["entity"]
                console.print(
                    f"  depth={c['depth']} [cyan]{e['kind']}[/cyan] {e['qualified_name']} [{e['file']}:{e['line']}]"
                )
    db.close()


# ── tkg callees ───────────────────────────────────────────────────────────────


@main.command()
@click.argument("qualified_name")
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--depth", default=1)
@click.option("--json", "as_json", is_flag=True)
def callees(qualified_name: str, repo: str, depth: int, as_json: bool) -> None:
    """Find all functions called by a function."""
    db, engine = _get_engine(repo)
    result = engine.get_callees(qualified_name, depth)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
        else:
            console.print(f"[bold]Callees of:[/bold] {result['source']['qualified_name']}")
            for c in result["callees"]:
                e = c["entity"]
                console.print(
                    f"  depth={c['depth']} [cyan]{e['kind']}[/cyan] {e['qualified_name']} [{e['file']}:{e['line']}]"
                )
    db.close()


# ── tkg routes ────────────────────────────────────────────────────────────────


@main.command()
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True)
def routes(repo: str, as_json: bool) -> None:
    """List all HTTP API routes."""
    db, engine = _get_engine(repo)
    result = engine.list_api_routes()
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        t = Table(title=f"API Routes ({result['count']})")
        t.add_column("Method", style="green", width=8)
        t.add_column("Path", style="bold")
        t.add_column("File:Line", style="dim")
        for r in result["routes"]:
            extra = r.get("extra", {})
            t.add_row(
                extra.get("method", "?"),
                extra.get("path", r["name"]),
                f"{r['file']}:{r['line_start']}",
            )
        console.print(t)
    db.close()


# ── tkg tables ────────────────────────────────────────────────────────────────


@main.command()
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True)
def tables(repo: str, as_json: bool) -> None:
    """List all SQL tables."""
    db, engine = _get_engine(repo)
    result = engine.list_sql_tables()
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        t = Table(title=f"SQL Tables ({result['count']})")
        t.add_column("Table", style="bold")
        t.add_column("Columns", style="dim")
        t.add_column("File:Line", style="dim")
        for tbl in result["tables"]:
            cols = ", ".join(c["name"] for c in tbl.get("columns", [])[:8])
            if len(tbl.get("columns", [])) > 8:
                cols += " ..."
            t.add_row(tbl["name"], cols, f"{tbl['file']}:{tbl['line_start']}")
        console.print(t)
    db.close()


# ── tkg context ───────────────────────────────────────────────────────────────


@main.command()
@click.argument("task")
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--budget", default=4000, help="Token budget")
@click.option("--seed", multiple=True, help="Seed entity qualified names")
@click.option("--json", "as_json", is_flag=True)
def context(task: str, repo: str, budget: int, seed: tuple, as_json: bool) -> None:
    """Build a token-budgeted context pack for a task."""
    db, engine = _get_engine(repo)
    result = engine.context_pack(task, budget, list(seed) or None)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"[bold]Task:[/bold] {result['task']}")
        console.print(
            f"[dim]Tokens used: {result['tokens_used']}/{result['token_budget']} | "
            f"Entities: {result['entity_count']}[/dim]\n"
        )
        console.print(Syntax(result["context"], "python", theme="monokai", line_numbers=False))
    db.close()


# ── tkg stats ─────────────────────────────────────────────────────────────────


@main.command()
@click.option("--repo", default=".", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True)
def stats(repo: str, as_json: bool) -> None:
    """Show index statistics."""
    db, engine = _get_engine(repo)
    result = engine.get_stats()
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        t = Table(title="Knowledge Graph Stats")
        t.add_column("Metric", style="cyan")
        t.add_column("Value", justify="right")
        t.add_row("Repository", result.get("repo_root", "?"))
        t.add_row("Last updated", result.get("last_updated", "?"))
        t.add_row("Schema version", result.get("schema_version", "?"))
        t.add_row("Entities", str(result.get("entities", 0)))
        t.add_row("Relations", str(result.get("relations", 0)))
        t.add_row("Files", str(result.get("files", 0)))
        for lang, count in (result.get("languages") or {}).items():
            t.add_row(f"  {lang}", str(count))
        console.print(t)
    db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Business Intelligence Engine CLI
# ═══════════════════════════════════════════════════════════════════════════════


def _get_biz_engine(repo: str) -> tuple[GraphDB, BizQueryEngine]:
    repo_path = Path(repo).resolve()
    db_path = repo_path / ".tkg" / "graph.db"
    biz_repo = open_repository(db_path)
    db = GraphDB(db_path)
    db.biz_repo = biz_repo
    db.connect()
    return db, BizQueryEngine(biz_repo)


def _get_ede(repo: str) -> tuple[GraphDB, ExecutiveDecisionEngine]:
    repo_path = Path(repo).resolve()
    db_path = repo_path / ".tkg" / "graph.db"
    biz_repo = open_repository(db_path)
    db = GraphDB(db_path)
    db.biz_repo = biz_repo
    db.connect()
    return db, ExecutiveDecisionEngine(biz_repo)


# ── tkg biz-import ────────────────────────────────────────────────────────────


@main.command("biz-import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--repo", default=".", show_default=True, help="Repository root")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["auto", "csv", "json", "tenderscope"]),
    default="auto",
    show_default=True,
)
@click.option("--entity-kind", default=None, help="Entity kind override for CSV (e.g. company)")
@click.option("--name-column", default=None, help="Name column for CSV imports")
@click.option("--attr-columns", default="", help="Comma-separated attribute column names for CSV")
@click.option("--source-tag", default="cli", show_default=True)
@click.option("--limit", default=None, type=int, help="Max rows to import (for testing)")
@click.option("--json", "as_json", is_flag=True)
def biz_import(
    file: str,
    repo: str,
    fmt: str,
    entity_kind: str | None,
    name_column: str | None,
    attr_columns: str,
    source_tag: str,
    limit: int | None,
    as_json: bool,
) -> None:
    """Import business entities from a CSV, JSON, or TenderScope file."""
    db, _ = _get_biz_engine(repo)
    biz_repo = db.biz_repo
    p = Path(file).resolve()
    suffix = p.suffix.lower()

    if fmt == "auto":
        fmt = "csv" if suffix == ".csv" else ("json" if suffix == ".json" else "tenderscope")

    if fmt == "csv":
        if not entity_kind or not name_column:
            click.echo("Error: --entity-kind and --name-column are required for CSV format", err=True)
            sys.exit(1)
        schema = {
            "entity_kind": entity_kind,
            "name_column": name_column,
            "attribute_columns": [c.strip() for c in attr_columns.split(",") if c.strip()],
        }
        importer = CSVImporter(biz_repo, str(p), schema=schema, source_tag=source_tag)
    elif fmt == "json":
        importer = JSONImporter(biz_repo, str(p), source_tag=source_tag)
    else:
        importer = TenderScopeImporter(biz_repo, str(p), source_tag=source_tag, limit=limit)

    result = importer.run()
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[bold green]Import complete[/bold green]: {result.importer}")
        console.print(f"  Entities created : {result.entities_created}")
        console.print(f"  Entities updated : {result.entities_updated}")
        console.print(f"  Relations created: {result.relations_created}")
        console.print(f"  Relations updated: {result.relations_updated}")
        console.print(f"  Elapsed          : {result.elapsed_s:.2f}s")
        if result.errors:
            console.print(f"  [red]Errors ({len(result.errors)})[/red]:", result.errors[:5])
        if result.warnings:
            console.print(f"  [yellow]Warnings ({len(result.warnings)})[/yellow]:", result.warnings[:5])
    db.close()


# ── tkg biz-search ────────────────────────────────────────────────────────────


@main.command("biz-search")
@click.argument("query")
@click.option("--repo", default=".", show_default=True)
@click.option("--kind", default=None, help="Filter by entity kind (company, tender, …)")
@click.option("--limit", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def biz_search(query: str, repo: str, kind: str | None, limit: int, as_json: bool) -> None:
    """Search business entities by name or keyword."""
    db, engine = _get_biz_engine(repo)
    kinds = [kind] if kind else None
    result = engine.search(query, kinds=kinds, limit=limit)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        t = Table(title=f"Business search: '{query}'")
        t.add_column("UID", style="cyan", no_wrap=True)
        t.add_column("Kind", style="magenta")
        t.add_column("Name")
        t.add_column("Source")
        for r in result["results"]:
            t.add_row(r["uid"], r["kind"], r["name"], r.get("source") or "")
        console.print(t)
        console.print(f"  {result['count']} result(s)")
    db.close()


# ── tkg biz-entity ────────────────────────────────────────────────────────────


@main.command("biz-entity")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def biz_entity(uid: str, repo: str, as_json: bool) -> None:
    """Show full detail for a business entity by UID."""
    db, engine = _get_biz_engine(repo)
    result = engine.entity(uid)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
        else:
            e = result["entity"]
            console.print(f"\n[bold cyan]{e['uid']}[/bold cyan]  [{e['kind']}]  {e['name']}")
            console.print(f"  Canonical : {e['canonical_name']}")
            console.print(f"  Source    : {e.get('source') or '—'}")
            console.print(f"  Confidence: {e.get('confidence', 1.0):.2f}")
            if e.get("attributes"):
                console.print("  Attributes:")
                for k, v in e["attributes"].items():
                    console.print(f"    {k}: {v}")
            console.print(f"\n  Connections ({result.get('total_connections', len(result['neighbors']))}):")
            for nb in result["neighbors"][:20]:
                rel = nb.get("relation", "?")
                ent = nb.get("entity", nb)
                console.print(f"    [{rel}] {ent.get('uid', '?')} — {ent.get('name', '?')}")
    db.close()


# ── tkg biz-neighbors ─────────────────────────────────────────────────────────


@main.command("biz-neighbors")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--direction", type=click.Choice(["both", "out", "in"]), default="both")
@click.option("--kind", "kinds", multiple=True, help="Relation kind filter (repeatable)")
@click.option("--limit", default=50, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def biz_neighbors(uid: str, repo: str, direction: str, kinds: tuple, limit: int, as_json: bool) -> None:
    """Show all neighbors of a business entity."""
    db, engine = _get_biz_engine(repo)
    result = engine.neighbors(uid, direction=direction, kinds=list(kinds) or None, limit=limit)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
        else:
            console.print(f"\nNeighbors of [cyan]{uid}[/cyan] ({direction}) — {result['count']} found")
            t = Table()
            t.add_column("Relation", style="yellow")
            t.add_column("UID", style="cyan")
            t.add_column("Kind", style="magenta")
            t.add_column("Name")
            for nb in result["neighbors"]:
                rel = nb["relation"].get("kind", "?")
                ent = nb["entity"]
                t.add_row(rel, ent["uid"], ent["kind"], ent["name"])
            console.print(t)
    db.close()


# ── tkg biz-path ──────────────────────────────────────────────────────────────


@main.command("biz-path")
@click.argument("uid1")
@click.argument("uid2")
@click.option("--repo", default=".", show_default=True)
@click.option("--max-depth", default=6, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def biz_path(uid1: str, uid2: str, repo: str, max_depth: int, as_json: bool) -> None:
    """Find a path between two business entities in the knowledge graph."""
    db, engine = _get_biz_engine(repo)
    result = engine.find_path(uid1, uid2, max_depth=max_depth)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        if not result.get("found"):
            console.print(f"[yellow]{result.get('message', 'No path found')}[/yellow]")
        else:
            console.print(f"\nPath found in [bold]{result['hops']}[/bold] hop(s):")
            for i, node in enumerate(result["path"]):
                via = node.get("via")
                uid = node.get("uid", "?")
                name = node.get("name", "?")
                kind = node.get("kind", "?")
                if via and i > 0:
                    console.print(f"  [yellow]──[{via}]──▶[/yellow]")
                console.print(f"  [cyan]{uid}[/cyan]  [{kind}]  {name}")
    db.close()


# ── tkg biz-stats ─────────────────────────────────────────────────────────────


@main.command("biz-stats")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def biz_stats(repo: str, as_json: bool) -> None:
    """Show business graph statistics."""
    db, engine = _get_biz_engine(repo)
    result = engine.graph_statistics()
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        biz = result.get("business_graph", {})
        t = Table(title="Business Graph Stats")
        t.add_column("Metric", style="cyan")
        t.add_column("Value", justify="right")
        t.add_row("Total entities", str(biz.get("entities", 0)))
        t.add_row("Total relations", str(biz.get("relations", 0)))
        t.add_row("History entries", str(biz.get("history_entries", 0)))
        for kind, count in (biz.get("by_kind") or {}).items():
            t.add_row(f"  {kind}", str(count))
        console.print(t)
        seqs = biz.get("sequences") or {}
        if seqs:
            console.print("\n[bold]UID sequences (highest allocated):[/bold]")
            for prefix, val in sorted(seqs.items()):
                console.print(f"  {prefix}: {val:,}")
    db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Company Intelligence Engine commands
# ══════════════════════════════════════════════════════════════════════════════


def _get_cie(repo: str) -> tuple[object, CompanyIntelligenceEngine]:
    db, _ = _get_biz_engine(repo)
    return db, CompanyIntelligenceEngine(db.biz_repo)


def _get_rie(repo: str) -> tuple[object, RelationshipIntelligenceEngine]:
    db, _ = _get_biz_engine(repo)
    return db, RelationshipIntelligenceEngine(db.biz_repo)


# ── tkg cie-profile ───────────────────────────────────────────────────────────


@main.command("cie-profile")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cie_profile(uid: str, repo: str, as_json: bool) -> None:
    """Full explainable company profile (all sub-queries)."""
    db, cie = _get_cie(repo)
    result = cie.company_profile(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        s = result.get("summary", {})
        console.print(f"\n[bold cyan]{result['name']}[/bold cyan]  [dim]{result['uid']}[/dim]")
        console.print(f"  Source          : {result.get('source', 'n/a')}")
        console.print(f"  Confidence      : {s.get('confidence_score', 'n/a')}")
        console.print(f"  Evidence edges  : {s.get('evidence_count', 0)}")
        console.print(f"  Tenders won     : {s.get('tenders_won', 0)}")
        console.print(f"  Total value     : ${s.get('total_awarded_value', 0):,.2f}")
        console.print(f"  Largest contract: ${s.get('largest_contract', 0):,.2f}")
        console.print(f"  Unique buyers   : {s.get('unique_buyers', 0)}")
        console.print(f"  Locations       : {', '.join(s.get('locations', []) or ['n/a'])}")
        console.print(f"  Industries      : {', '.join(s.get('industries', []) or ['n/a'])}")
        console.print(f"  First activity  : {s.get('first_activity', 'n/a')}")
        console.print(f"  Latest activity : {s.get('latest_activity', 'n/a')}")
    db.close()


# ── tkg cie-summary ───────────────────────────────────────────────────────────


@main.command("cie-summary")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cie_summary(uid: str, repo: str, as_json: bool) -> None:
    """Lightweight company overview with confidence score."""
    db, cie = _get_cie(repo)
    result = cie.company_summary(uid)
    click.echo(json.dumps(result, indent=2))
    db.close()


# ── tkg cie-tenders ───────────────────────────────────────────────────────────


@main.command("cie-tenders")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=200, show_default=True)
def cie_tenders(uid: str, repo: str, as_json: bool, limit: int) -> None:
    """All tenders won and submitted by a company."""
    db, cie = _get_cie(repo)
    result = cie.company_tenders(uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('company')}[/bold]  [dim]{uid}[/dim]")
        console.print(f"Tenders won: {result['tenders_won_count']}  |  Submitted: {result['tenders_submitted_count']}")
        if result["tenders_won"]:
            t = Table(title="Tenders Won", show_lines=False)
            t.add_column("UID", style="dim", width=16)
            t.add_column("Name")
            t.add_column("Value", justify="right")
            t.add_column("Date")
            for row in result["tenders_won"][:50]:
                t.add_row(
                    row["uid"],
                    (row["name"] or "")[:60],
                    f"${row['contract_value']:,.0f}" if row.get("contract_value") else "",
                    row.get("award_date") or "",
                )
            console.print(t)
    db.close()


# ── tkg cie-contracts ─────────────────────────────────────────────────────────


@main.command("cie-contracts")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=100, show_default=True)
def cie_contracts(uid: str, repo: str, as_json: bool, limit: int) -> None:
    """Awarded contracts sorted by value."""
    db, cie = _get_cie(repo)
    result = cie.company_contracts(uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('company')}[/bold]  [dim]{uid}[/dim]")
        console.print(
            f"Contracts: {result['contract_count']}  |  Total: ${result['total_value']:,.2f}"
            f"  |  Avg: ${result['average_value']:,.2f}"
        )
        t = Table(title="Awarded Contracts", show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Tender / Contract")
        t.add_column("Value", justify="right")
        t.add_column("Date")
        for row in result.get("contracts", [])[:50]:
            t.add_row(
                row["uid"],
                (row["name"] or "")[:60],
                f"${row['contract_value']:,.0f}" if row.get("contract_value") else "",
                row.get("award_date") or "",
            )
        console.print(t)
    db.close()


# ── tkg cie-competitors ───────────────────────────────────────────────────────


@main.command("cie-competitors")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
def cie_competitors(uid: str, repo: str, as_json: bool, limit: int) -> None:
    """Competitor companies ranked by shared buyers/tenders."""
    db, cie = _get_cie(repo)
    result = cie.company_competitors(uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('company')}[/bold]  competitors")
        t = Table(title="Competitors", show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Shared tenders", justify="right")
        t.add_column("Shared buyers", justify="right")
        for comp in result.get("competitors", []):
            t.add_row(
                comp["uid"],
                comp["name"][:50],
                str(len(comp.get("shared_tenders", []))),
                str(len(comp.get("shared_buyers", []))),
            )
        console.print(t)
    db.close()


# ── tkg cie-timeline ──────────────────────────────────────────────────────────


@main.command("cie-timeline")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cie_timeline(uid: str, repo: str, as_json: bool) -> None:
    """Chronological activity timeline for a company."""
    db, cie = _get_cie(repo)
    result = cie.company_timeline(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('company')}[/bold] — timeline ({result['event_count']} events)")
        t = Table(show_lines=False)
        t.add_column("Date")
        t.add_column("Event")
        t.add_column("Counterpart")
        t.add_column("Value", justify="right")
        for ev in result.get("events", []):
            t.add_row(
                ev["date"],
                ev["event_type"],
                (ev["counterpart_name"] or "")[:50],
                f"${ev['value']:,.0f}" if ev.get("value") else "",
            )
        console.print(t)
        yearly = result.get("yearly_activity", {})
        if yearly:
            console.print("\n[bold]Yearly activity:[/bold]")
            for yr, cnt in sorted(yearly.items()):
                console.print(f"  {yr}: {cnt} events")
    db.close()


# ── tkg cie-most-connected ────────────────────────────────────────────────────


@main.command("cie-most-connected")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
def cie_most_connected(repo: str, as_json: bool, limit: int) -> None:
    """Rank companies by total graph edge count."""
    db, cie = _get_cie(repo)
    result = cie.most_connected_companies(limit=limit)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        t = Table(title="Most Connected Companies", show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Out edges", justify="right")
        t.add_column("In edges", justify="right")
        t.add_column("Total", justify="right", style="bold")
        for row in result.get("companies", []):
            t.add_row(
                row["uid"],
                row["name"][:50],
                str(row["out_edges"]),
                str(row["in_edges"]),
                str(row["total_edges"]),
            )
        console.print(t)
    db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Relationship Intelligence Engine commands
# ══════════════════════════════════════════════════════════════════════════════

# ── tkg rie-explain ───────────────────────────────────────────────────────────


@main.command("rie-explain")
@click.argument("uid_a")
@click.argument("uid_b")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def rie_explain(uid_a: str, uid_b: str, repo: str, as_json: bool) -> None:
    """Explain WHY two entities are connected."""
    db, rie = _get_rie(repo)
    result = rie.explain(uid_a, uid_b)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        a = result["uid_a"]
        b = result["uid_b"]
        console.print(f"\n[bold cyan]{a['name']}[/bold cyan] ↔ [bold cyan]{b['name']}[/bold cyan]")
        console.print(f"  Strength  : [bold]{result['relationship_strength']}[/bold]")
        console.print(f"  Confidence: {result['confidence']}")
        console.print(f"  Evidence  : {result['evidence_count']} signals")
        console.print("\n[bold]Explanation:[/bold]")
        console.print(f"  {result['explanation_text']}")
        if result.get("direct_relations"):
            console.print(f"\n[bold]Direct edges:[/bold] {len(result['direct_relations'])}")
        if result.get("shared_buyers"):
            names = ", ".join(b["name"] for b in result["shared_buyers"][:5])
            console.print(f"[bold]Shared buyers:[/bold] {names}")
        if result.get("shared_industries"):
            names = ", ".join(i["name"] for i in result["shared_industries"][:5])
            console.print(f"[bold]Shared industries:[/bold] {names}")
        if result.get("shared_locations"):
            names = ", ".join(loc["name"] for loc in result["shared_locations"][:5])
            console.print(f"[bold]Shared locations:[/bold] {names}")
        if result.get("shortest_path"):
            console.print("\n[bold]Shortest path:[/bold]")
            console.print(f"  {result.get('explanation_text', '')}")
    db.close()


# ── tkg rie-strength ──────────────────────────────────────────────────────────


@main.command("rie-strength")
@click.argument("uid_a")
@click.argument("uid_b")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def rie_strength(uid_a: str, uid_b: str, repo: str, as_json: bool) -> None:
    """Weighted relationship strength between two entities."""
    db, rie = _get_rie(repo)
    result = rie.relationship_strength(uid_a, uid_b)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name_a']}[/bold] ↔ [bold]{result['name_b']}[/bold]")
        console.print(f"  Strength  : [bold]{result['relationship_strength']}[/bold]")
        console.print(f"  Confidence: {result['confidence']}")
        console.print(f"  Evidence  : {result['evidence_count']} signals\n")
        t = Table(title="Signal Breakdown", show_lines=False)
        t.add_column("Signal")
        t.add_column("Count", justify="right")
        t.add_column("Strength", justify="right")
        for signal, data in result.get("breakdown", {}).items():
            t.add_row(signal, str(data["count"]), str(data["strength"]))
        console.print(t)
    db.close()


# ── tkg rie-path ──────────────────────────────────────────────────────────────


@main.command("rie-path")
@click.argument("uid_a")
@click.argument("uid_b")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--max-depth", default=8, show_default=True)
def rie_path(uid_a: str, uid_b: str, repo: str, as_json: bool, max_depth: int) -> None:
    """BFS shortest path between two entities."""
    db, rie = _get_rie(repo)
    result = rie.shortest_path(uid_a, uid_b, max_depth=max_depth)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        if not result["found"]:
            console.print(f"[yellow]{result['path_string']}[/yellow]")
        else:
            console.print(f"\n[bold]Path ({result['hop_count']} hops):[/bold]")
            console.print(f"  {result['path_string']}")
    db.close()


# ── tkg rie-infer ─────────────────────────────────────────────────────────────


@main.command("rie-infer")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
def rie_infer(uid: str, repo: str, as_json: bool, limit: int) -> None:
    """Infer all indirect relationships for an entity."""
    db, rie = _get_rie(repo)
    result = rie.infer_relationships(uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]  — {result['inferred_count']} inferences")
        for section, label in [
            ("shared_buyer_links", "Shared-buyer links"),
            ("subcontractor_hints", "Subcontractor hints"),
            ("partnership_hints", "Recurring partnerships"),
            ("industry_cluster_peers", "Industry cluster peers"),
            ("geographic_cluster_peers", "Geographic cluster peers"),
        ]:
            items = result.get(section, [])
            if not items:
                continue
            t = Table(title=label, show_lines=False)
            t.add_column("UID", style="dim", width=16)
            t.add_column("Name")
            t.add_column("Strength", justify="right")
            t.add_column("Confidence", justify="right")
            for item in items[:10]:
                t.add_row(
                    item["uid"],
                    item["name"][:50],
                    str(item.get("strength", "")),
                    str(item.get("confidence", "")),
                )
            console.print(t)
    db.close()


# ── tkg rie-clusters ──────────────────────────────────────────────────────────


@main.command("rie-clusters")
@click.argument("location_or_industry")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=50, show_default=True)
@click.option("--geo", "is_geo", is_flag=True, help="Treat argument as geographic location (city/province)")
def rie_clusters(location_or_industry: str, repo: str, as_json: bool, limit: int, is_geo: bool) -> None:
    """Show companies in a geographic or industry cluster."""
    db, rie = _get_rie(repo)
    if is_geo:
        result = rie.geographic_clusters(location_or_industry, limit=limit)
    else:
        result = rie.industry_clusters(location_or_industry, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        title = result.get("industry_name") or result.get("location_name", location_or_industry)
        console.print(f"\n[bold]Cluster: {title}[/bold]  ({result['company_count']} companies)")
        t = Table(show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        for co in result.get("companies", [])[:limit]:
            t.add_row(co["uid"], co["name"][:60])
        console.print(t)
    db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Competitive Intelligence Engine commands
# ══════════════════════════════════════════════════════════════════════════════


def _get_cei(repo: str) -> tuple[object, CompetitiveIntelligenceEngine]:
    db, _ = _get_biz_engine(repo)
    return db, CompetitiveIntelligenceEngine(db.biz_repo)


# ── tkg cei-profile ───────────────────────────────────────────────────────────


@main.command("cei-profile")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cei_profile(uid: str, repo: str, as_json: bool) -> None:
    """Full competitive profile for a company."""
    db, cei = _get_cei(repo)
    result = cei.competitor_profile(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold cyan]{result['name']}[/bold cyan]  [{uid}]")
        wr = result.get("win_rate", {})
        console.print(
            f"  Wins: {wr.get('wins', 0)}  |  Bids: {wr.get('bids', 0)}  |  Win Rate: {wr.get('win_rate', 0):.1%}"
        )
        pressure = result.get("competitive_pressure", {})
        score = pressure.get("competitive_pressure_score", 0)
        level = pressure.get("pressure_level", "?")
        console.print(f"  Pressure: [bold]{score:.3f}[/bold]  ({level})")
        dc = result.get("direct_competitors", {})
        console.print(f"  Direct competitors: {dc.get('competitor_count', 0)}")
    db.close()


# ── tkg cei-win-rate ──────────────────────────────────────────────────────────


@main.command("cei-win-rate")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cei_win_rate(uid: str, repo: str, as_json: bool) -> None:
    """Win rate, loss rate, bid frequency for a company."""
    db, cei = _get_cei(repo)
    result = cei.win_rate(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        console.print(f"  Wins:           {result['wins']}")
        console.print(f"  Bids (non-win): {result['bids']}")
        console.print(f"  Participations: {result['participations']}")
        console.print(f"  Win rate:       [bold]{result['win_rate']:.1%}[/bold]")
        console.print(f"  Loss rate:      {result['loss_rate']:.1%}")
        console.print(f"  Bid frequency:  {result['bid_frequency']}")
    db.close()


# ── tkg cei-growth ────────────────────────────────────────────────────────────


@main.command("cei-growth")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cei_growth(uid: str, repo: str, as_json: bool) -> None:
    """Year-over-year growth trend for a company."""
    db, cei = _get_cei(repo)
    result = cei.growth_trend(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  — Trend: [bold]{result['trend']}[/bold]")
        t = Table(title="Activity by Year", show_lines=False)
        t.add_column("Year", justify="right")
        t.add_column("Wins", justify="right", style="green")
        t.add_column("Bids", justify="right")
        t.add_column("Total", justify="right", style="bold")
        for row in result.get("timeline", []):
            t.add_row(str(row["year"]), str(row["wins"]), str(row["bids"]), str(row["total"]))
        console.print(t)
    db.close()


# ── tkg cei-competitors ───────────────────────────────────────────────────────


@main.command("cei-competitors")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
@click.option("--emerging", is_flag=True, help="Show emerging competitors instead of direct")
def cei_competitors(uid: str, repo: str, as_json: bool, limit: int, emerging: bool) -> None:
    """Direct or emerging competitors for a company."""
    db, cei = _get_cei(repo)
    result = cei.emerging_competitors(uid, limit=limit) if emerging else cei.direct_competitors(uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        key = "emerging_competitors" if emerging else "competitors"
        count_key = "emerging_count" if emerging else "competitor_count"
        title = "Emerging Competitors" if emerging else "Direct Competitors"
        console.print(f"\n[bold]{result['name']}[/bold]  — {result[count_key]} {title}")
        t = Table(title=title, show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Score" if not emerging else "Growth", justify="right")
        for item in result.get(key, []):
            score = str(item.get("competition_score") or item.get("growth_ratio", ""))
            t.add_row(item["uid"], item["name"][:50], score)
        console.print(t)
    db.close()


# ── tkg cei-market-share ──────────────────────────────────────────────────────


@main.command("cei-market-share")
@click.argument("scope_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--by",
    default="company",
    show_default=True,
    type=click.Choice(["company", "year", "buyer", "city", "province", "industry"]),
)
@click.option("--limit", default=20, show_default=True)
def cei_market_share(scope_uid: str, repo: str, as_json: bool, by: str, limit: int) -> None:
    """Market share breakdown for a scope (buyer org, industry, city, province)."""
    db, cei = _get_cei(repo)
    result = cei.market_share(scope_uid, by=by, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['scope_name']}[/bold]  — Market Share by {by}")
        console.print(f"  Total tenders: {result['total_tenders']}")
        t = Table(show_lines=False)
        t.add_column(by.title())
        t.add_column("Count", justify="right")
        t.add_column("Share", justify="right", style="bold")
        for row in result.get("shares", [])[:limit]:
            t.add_row(str(row["label"]), str(row["count"]), f"{row['share']:.1%}")
        console.print(t)
    db.close()


# ── tkg cei-rankings ──────────────────────────────────────────────────────────


@main.command("cei-rankings")
@click.argument("scope_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--by",
    default="wins",
    show_default=True,
    type=click.Choice(["wins", "bids", "win_rate", "market_share"]),
)
@click.option("--limit", default=20, show_default=True)
def cei_rankings(scope_uid: str, repo: str, as_json: bool, by: str, limit: int) -> None:
    """Ranked companies for a market scope."""
    db, cei = _get_cei(repo)
    result = cei.competitor_rankings(scope_uid, by=by, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['scope_name']}[/bold]  — Rankings by {by}")
        t = Table(show_lines=False)
        t.add_column("#", justify="right", style="dim")
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Wins", justify="right")
        t.add_column("Win Rate", justify="right")
        t.add_column("Share", justify="right")
        for i, row in enumerate(result.get("rankings", []), 1):
            t.add_row(
                str(i),
                row["uid"],
                row["name"][:48],
                str(row["wins"]),
                f"{row['win_rate']:.1%}",
                f"{row['market_share']:.1%}",
            )
        console.print(t)
    db.close()


# ── tkg cei-pressure ──────────────────────────────────────────────────────────


@main.command("cei-pressure")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def cei_pressure(uid: str, repo: str, as_json: bool) -> None:
    """Composite competitive pressure score for a company."""
    db, cei = _get_cei(repo)
    result = cei.competitive_pressure(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        console.print(
            f"  Pressure score: [bold]{result['competitive_pressure_score']:.3f}[/bold]  ({result['pressure_level']})"
        )
        t = Table(title="Components", show_lines=False)
        t.add_column("Component")
        t.add_column("Value", justify="right")
        for k, v in result.get("components", {}).items():
            t.add_row(k, f"{v:.4f}")
        console.print(t)
    db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Buyer Intelligence Engine commands
# ══════════════════════════════════════════════════════════════════════════════


def _get_bie(repo: str) -> tuple[object, BuyerIntelligenceEngine]:
    db, _ = _get_biz_engine(repo)
    return db, BuyerIntelligenceEngine(db.biz_repo)


# ── tkg bie-profile ────────────────────────────────────────────────────────


@main.command("bie-profile")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def bie_profile(uid: str, repo: str, as_json: bool) -> None:
    """Full buyer intelligence profile for a procurement organisation."""
    db, bie = _get_bie(repo)
    result = bie.buyer_profile(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        s = result.get("summary", {})
        console.print(f"\n[bold cyan]{result['name']}[/bold cyan]  [{uid}]  ({result['kind']})")
        console.print(f"  Tenders:          {s.get('total_tenders', 0)}")
        console.print(f"  Active suppliers: {s.get('active_suppliers', 0)}")
        console.print(f"  Award HHI:        {s.get('award_hhi', 0):.4f}")
        bc = result.get("buyer_competitiveness", {})
        score = bc.get("competitiveness_score", 0)
        level = bc.get("competitiveness_level", "?")
        console.print(f"  Competitiveness:  [bold]{score:.3f}[/bold]  ({level})")
    db.close()


# ── tkg bie-suppliers ────────────────────────────────────────────────────


@main.command("bie-suppliers")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
@click.option("--preferred", is_flag=True, help="Show only preferred suppliers (awarded ≥2 times)")
@click.option("--min-awards", default=2, show_default=True)
def bie_suppliers(uid: str, repo: str, as_json: bool, limit: int, preferred: bool, min_awards: int) -> None:
    """Supplier roster or preferred suppliers for a buyer."""
    db, bie = _get_bie(repo)
    result = (
        bie.preferred_suppliers(uid, min_awards=min_awards, limit=limit)
        if preferred
        else bie.supplier_roster(uid, limit=limit)
    )
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        key = "preferred_suppliers" if preferred else "suppliers"
        title = "Preferred Suppliers" if preferred else "Supplier Roster"
        count = result.get("preferred_supplier_count" if preferred else "supplier_count", 0)
        console.print(f"\n[bold]{result['name']}[/bold]  — {count} {title}")
        t = Table(title=title, show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Awards", justify="right", style="green")
        t.add_column("Win Rate", justify="right")
        for s in result.get(key, []):
            t.add_row(s["uid"], s["name"][:50], str(s["award_count"]), f"{s['win_rate']:.1%}")
        console.print(t)
    db.close()


# ── tkg bie-patterns ─────────────────────────────────────────────────────


@main.command("bie-patterns")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--seasonality", is_flag=True, help="Show monthly/quarterly breakdown")
def bie_patterns(uid: str, repo: str, as_json: bool, seasonality: bool) -> None:
    """Buying patterns or seasonality for a procurement organisation."""
    db, bie = _get_bie(repo)
    result = bie.procurement_seasonality(uid) if seasonality else bie.buying_patterns(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    elif seasonality:
        console.print(f"\n[bold]{result['name']}[/bold]  — Procurement Seasonality")
        t = Table(show_lines=False)
        t.add_column("Month")
        t.add_column("Count", justify="right")
        t.add_column("Share", justify="right")
        t.add_column("Seasonality Index", justify="right")
        for row in result.get("monthly", []):
            t.add_row(
                row["month_name"],
                str(row["count"]),
                f"{row['share']:.1%}",
                f"{row['seasonality_index']:+.3f}",
            )
        console.print(t)
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  — Buying Patterns")
        console.print(f"  Total tenders:    {result['total_tenders']}")
        console.print(f"  Avg value:        {result['avg_value']}")
        console.print(f"  Avg bidders:      {result['avg_bidder_count']}")
        console.print(f"  Peak month:       {result['peak_month_name']}")
        console.print(f"  Busiest year:     {result['busiest_year']}")
        console.print(f"  Cadence (months): {result['cadence_months']}")
    db.close()


# ── tkg bie-timeline ─────────────────────────────────────────────────────


@main.command("bie-timeline")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def bie_timeline(uid: str, repo: str, as_json: bool) -> None:
    """Year-by-year procurement timeline for a buyer."""
    db, bie = _get_bie(repo)
    result = bie.buyer_timeline(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  — Trend: [bold]{result['trend']}[/bold]")
        t = Table(title="Procurement Timeline", show_lines=False)
        t.add_column("Year", justify="right")
        t.add_column("Tenders", justify="right")
        t.add_column("Suppliers", justify="right")
        t.add_column("Winners", justify="right")
        t.add_column("Avg Value", justify="right")
        t.add_column("Top Winner")
        for row in result.get("timeline", []):
            tw = row.get("top_winner") or {}
            t.add_row(
                str(row["year"]),
                str(row["tender_count"]),
                str(row["unique_suppliers"]),
                str(row["unique_winners"]),
                str(row["avg_value"]) if row["avg_value"] else "",
                tw.get("name", "")[:30],
            )
        console.print(t)
    db.close()


# ── tkg bie-score ──────────────────────────────────────────────────────────


@main.command("bie-score")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--diversity", is_flag=True, help="Show supplier diversity instead of competitiveness")
def bie_score(uid: str, repo: str, as_json: bool, diversity: bool) -> None:
    """Buyer competitiveness or supplier diversity score."""
    db, bie = _get_bie(repo)
    result = bie.supplier_diversity(uid) if diversity else bie.buyer_competitiveness(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    elif diversity:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        console.print(f"  Diversity score: [bold]{result['diversity_score']:.3f}[/bold]  ({result['diversity_level']})")
        console.print(f"  Unique suppliers: {result['unique_suppliers']}  —  HHI: {result['award_hhi']:.4f}")
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        score = result["competitiveness_score"]
        level = result["competitiveness_level"]
        console.print(f"  Competitiveness: [bold]{score:.3f}[/bold]  ({level})")
        t = Table(title="Components", show_lines=False)
        t.add_column("Component")
        t.add_column("Value", justify="right")
        for k, v in result.get("components", {}).items():
            t.add_row(k, f"{v:.4f}")
        console.print(t)
    db.close()


# ── tkg bie-forecast ─────────────────────────────────────────────────────


@main.command("bie-forecast")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def bie_forecast(uid: str, repo: str, as_json: bool) -> None:
    """Forecast probability and timing of next tender from a buyer."""
    db, bie = _get_bie(repo)
    result = bie.tender_forecast(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        prob = result["forecast_probability"]
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        console.print(f"  Forecast probability: [bold]{prob:.1%}[/bold]  (basis: {result['forecast_basis']})")
        console.print(f"  Cadence:              {result['cadence_months']} months")
        console.print(f"  Months since last:    {result['months_since_last']}")
        if result["estimated_next_year"]:
            console.print(
                f"  Est. next tender:     {result['estimated_next_month_name']} {result['estimated_next_year']}"
            )
    db.close()


# ── tkg bie-concentration ──────────────────────────────────────────────────


@main.command("bie-concentration")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def bie_concentration(uid: str, repo: str, as_json: bool) -> None:
    """Award concentration (HHI) and top suppliers for a buyer."""
    db, bie = _get_bie(repo)
    result = bie.award_concentration(uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        console.print(f"  HHI: [bold]{result['hhi']:.4f}[/bold]  ({result['concentration_level']})")
        console.print(f"  Total awards: {result['total_awards']}  —  Unique suppliers: {result['unique_suppliers']}")
        t = Table(title="Top Suppliers by Award Count", show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Awards", justify="right")
        t.add_column("Share", justify="right")
        for s in result.get("top_suppliers", []):
            t.add_row(s["uid"], s["name"][:50], str(s["awards"]), f"{s['share']:.1%}")
        console.print(t)
    db.close()


# ── tkg bie-loyalty ────────────────────────────────────────────────────────


@main.command("bie-loyalty")
@click.argument("uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=20, show_default=True)
def bie_loyalty(uid: str, repo: str, as_json: bool, limit: int) -> None:
    """Supplier loyalty index for a buyer."""
    db, bie = _get_bie(repo)
    result = bie.supplier_loyalty(uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result['name']}[/bold]  [{uid}]")
        score = result["overall_loyalty_score"]
        interp = result["loyalty_interpretation"]
        console.print(f"  Overall loyalty: [bold]{score:.3f}[/bold]  ({interp})")
        t = Table(title="Supplier Loyalty Index", show_lines=False)
        t.add_column("UID", style="dim", width=16)
        t.add_column("Name")
        t.add_column("Awards", justify="right")
        t.add_column("Loyalty Index", justify="right", style="bold")
        for s in result.get("supplier_loyalty", []):
            t.add_row(s["uid"], s["name"][:50], str(s["award_count"]), f"{s['loyalty_index']:.4f}")
        console.print(t)
    db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Opportunity Intelligence Engine commands
# ══════════════════════════════════════════════════════════════════════════════


def _get_oie(repo: str) -> tuple[object, OpportunityIntelligenceEngine]:
    db, _ = _get_biz_engine(repo)
    return db, OpportunityIntelligenceEngine(db.biz_repo)


# ── tkg oie-profile ────────────────────────────────────────────────────────


@main.command("oie-profile")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_profile(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """Full opportunity profile (score + recommendation + all dimensions)."""
    db, oie = _get_oie(repo)
    result = oie.opportunity_profile(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('tender_name', tender_uid)}[/bold]  [{tender_uid}]")
        console.print(f"  Company: {result.get('company_name', company_uid)}")
        score = result.get("score", 0)
        rec = result.get("recommendation", "")
        color = "green" if "Pursue" in rec else ("yellow" if rec in ("Monitor", "Strategic Investment") else "red")
        console.print(f"  Score: [bold]{score:.1f}/100[/bold]  Recommendation: [{color}]{rec}[/{color}]")
    db.close()


# ── tkg oie-score ──────────────────────────────────────────────────────────


@main.command("oie-score")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_score(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """0–100 opportunity score with dimension breakdown."""
    db, oie = _get_oie(repo)
    result = oie.opportunity_score(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        name = result.get("tender_name", tender_uid)
        score = result.get("score", 0)
        console.print(f"\n[bold]{name}[/bold]  Score: [bold]{score:.1f}/100[/bold]")
        t = Table(title="Score Breakdown", show_lines=False)
        t.add_column("Dimension")
        t.add_column("Points", justify="right")
        t.add_column("Weight", justify="right")
        t.add_column("Raw", justify="right")
        for dim, val in result.get("dimensions", {}).items():
            t.add_row(
                dim.replace("_", " ").title(),
                f"{val['score']:.1f}",
                str(val["weight"]),
                f"{val['raw']:.0%}",
            )
        console.print(t)
    db.close()


# ── tkg oie-recommend ──────────────────────────────────────────────────────


@main.command("oie-recommend")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_recommend(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """Recommendation label + why-pursue/ignore + next actions."""
    db, oie = _get_oie(repo)
    result = oie.opportunity_recommendation(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        rec = result.get("recommendation", "")
        color = "green" if "Pursue" in rec else ("yellow" if rec in ("Monitor", "Strategic Investment") else "red")
        console.print(
            f"\n  Recommendation: [{color}][bold]{rec}[/bold][/{color}]  (score {result.get('score', 0):.1f})"
        )
        for r in result.get("why_pursue", []):
            console.print(f"  [green]✓[/green] {r}")
        for r in result.get("why_ignore", []):
            console.print(f"  [red]✗[/red] {r}")
        for a in result.get("next_actions", []):
            console.print(f"  [bold]→[/bold] {a}")
    db.close()


# ── tkg oie-explain ────────────────────────────────────────────────────────


@main.command("oie-explain")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_explain(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """Full explainability report: evidence, assumptions, reasoning chain."""
    db, oie = _get_oie(repo)
    result = oie.opportunity_explain(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]Explainability Report[/bold]: {result.get('tender_name', tender_uid)}")
        for step in result.get("reasoning_chain", []):
            console.print(f"  {step}")
        if result.get("assumptions"):
            console.print("\n  [yellow]Assumptions:[/yellow]")
            for a in result["assumptions"]:
                console.print(f"    • {a}")
        if result.get("missing_information"):
            console.print("\n  [red]Missing information:[/red]")
            for m in result["missing_information"]:
                console.print(f"    • {m}")
    db.close()


# ── tkg oie-best ───────────────────────────────────────────────────────────


@main.command("oie-best")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=10, show_default=True)
def oie_best(company_uid: str, repo: str, as_json: bool, limit: int) -> None:
    """Top-N opportunities ranked by score for a company."""
    db, oie = _get_oie(repo)
    result = oie.best_opportunities(company_uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('company_name', company_uid)}[/bold]  — Top {limit} Opportunities")
        t = Table(title=f"Scored {result.get('total_tenders_scored', 0)} tenders", show_lines=False)
        t.add_column("#", justify="right", style="dim")
        t.add_column("Tender")
        t.add_column("Score", justify="right")
        t.add_column("Rec.")
        t.add_column("Value", justify="right")
        for i, opp in enumerate(result.get("top_opportunities", []), 1):
            val = f"${opp['value']:,.0f}" if opp.get("value") else "N/A"
            t.add_row(str(i), opp["tender_name"][:60], f"{opp['score']:.1f}", opp["recommendation"], val)
        console.print(t)
    db.close()


# ── tkg oie-timeline ───────────────────────────────────────────────────────


@main.command("oie-timeline")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_timeline(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """Preparation effort, submission urgency, deadline risk, comparable opportunities."""
    db, oie = _get_oie(repo)
    result = oie.opportunity_timeline(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]{result.get('tender_name', tender_uid)}[/bold]")
        urgency = result.get("submission_urgency", "unknown")
        prep = result.get("preparation_effort", "unknown")
        console.print(f"  Urgency: [bold]{urgency}[/bold]  — Prep: {prep}")
        console.print(
            f"  Deadline: {result.get('deadline', 'N/A')}  ({result.get('months_until_deadline', '?')} months)"
        )
        wins = len(result.get("comparable_wins", []))
        losses = len(result.get("comparable_losses", []))
        console.print(f"  Comparable wins: {wins}  — losses: {losses}")
    db.close()


# ── tkg oie-risk ───────────────────────────────────────────────────────────


@main.command("oie-risk")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_risk(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """Risk factors with severity and mitigation hints."""
    db, oie = _get_oie(repo)
    result = oie.opportunity_risk(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        name = result.get("tender_name", tender_uid)
        risk = result.get("overall_risk", "unknown")
        console.print(f"\n[bold]{name}[/bold]  Overall risk: [bold]{risk}[/bold]")
        t = Table(title="Risk Factors", show_lines=False)
        t.add_column("Factor")
        t.add_column("Severity")
        t.add_column("Detail")
        for rf in result.get("risk_factors", []):
            color = "red" if rf["severity"] == "high" else ("yellow" if rf["severity"] == "medium" else "green")
            t.add_row(rf["factor"], f"[{color}]{rf['severity']}[/{color}]", rf["detail"])
        console.print(t)
        for m in result.get("mitigations", []):
            console.print(f"  [bold]→[/bold] {m}")
    db.close()


# ── tkg oie-portfolio ──────────────────────────────────────────────────────


@main.command("oie-portfolio")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def oie_portfolio(company_uid: str, tender_uid: str, repo: str, as_json: bool) -> None:
    """Portfolio impact: expected revenue, diversification, strategic value."""
    db, oie = _get_oie(repo)
    result = oie.portfolio_impact(company_uid, tender_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        rev = f"${result['expected_revenue']:,.0f}" if result.get("expected_revenue") else "N/A"
        console.print(f"\n[bold]{result.get('tender_name', tender_uid)}[/bold]")
        console.print(f"  Expected revenue:     [bold]{rev}[/bold]")
        console.print(f"  Win probability:      {result.get('win_probability', 0):.0%}")
        console.print(f"  Diversification:      {result.get('diversification_impact', 'N/A')}")
        console.print(f"  Strategic value:      {result.get('strategic_value', 'N/A')}")
        console.print(f"  New client:           {result.get('is_new_client', False)}")
        console.print(f"  Future potential:     {result.get('future_relationship_potential', 'N/A')}")
    db.close()


# ── tkg oie-similar ────────────────────────────────────────────────────────


@main.command("oie-similar")
@click.argument("company_uid")
@click.argument("tender_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=10, show_default=True)
def oie_similar(company_uid: str, tender_uid: str, repo: str, as_json: bool, limit: int) -> None:
    """Similar historical opportunities with outcome (win/loss) and similarity score."""
    db, oie = _get_oie(repo)
    result = oie.similar_opportunities(company_uid, tender_uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(
            f"\n[bold]{result.get('tender_name', tender_uid)}[/bold]  — {result.get('similar_count', 0)} similar found"
        )
        t = Table(title="Similar Historical Opportunities", show_lines=False)
        t.add_column("Tender")
        t.add_column("Outcome")
        t.add_column("Similarity", justify="right")
        t.add_column("Value", justify="right")
        t.add_column("Reasons")
        for s in result.get("similar", []):
            color = "green" if s["outcome"] == "win" else "red"
            val = f"${s['value']:,.0f}" if s.get("value") else "N/A"
            t.add_row(
                s["name"][:50],
                f"[{color}]{s['outcome']}[/{color}]",
                f"{s['similarity']:.2f}",
                val,
                ", ".join(s.get("similarity_reasons", [])),
            )
        console.print(t)
    db.close()


# ── tkg oie-executive ──────────────────────────────────────────────────────


@main.command("oie-executive")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--limit", default=5, show_default=True)
def oie_executive(company_uid: str, repo: str, as_json: bool, limit: int) -> None:
    """CEO-dashboard executive summary: top opportunities, risks, next actions."""
    db, oie = _get_oie(repo)
    result = oie.executive_summary(company_uid, limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold]Executive Summary[/bold]: {result.get('company_name', company_uid)}")
        scored = result.get("total_tenders_scored", 0)
        confidence = result.get("confidence", 0)
        console.print(f"  Tenders scored: {scored}  — Confidence: {confidence:.2f}")
        if result.get("top_opportunities"):
            console.print("\n  [bold green]Top Opportunities:[/bold green]")
            for opp in result["top_opportunities"]:
                console.print(f"    [green]•[/green] {opp['tender_name'][:60]}  ({opp['score']:.1f})")
        if result.get("biggest_risks"):
            console.print("\n  [bold red]Biggest Risks:[/bold red]")
            for r in result["biggest_risks"]:
                console.print(f"    [red]•[/red] {r}")
        if result.get("immediate_next_actions"):
            console.print("\n  [bold yellow]Next Actions:[/bold yellow]")
            for a in result["immediate_next_actions"]:
                console.print(f"    → {a}")
        if result.get("opportunity_cost"):
            console.print(f"\n  Opportunity cost (ignored): ${result['opportunity_cost']:,.0f}")
    db.close()


# ────────────────────────────────────────────────────────────────────────────
# Executive Decision Engine commands
# ────────────────────────────────────────────────────────────────────────────


def _get_ede(repo: str) -> tuple:
    repo_path = Path(repo).resolve()
    db_path = repo_path / ".tkg" / "graph.db"
    db = GraphDB(db_path)
    db.connect()
    ede = ExecutiveDecisionEngine(db.biz_repo)
    return db, ede


@main.command("ede-decision")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--limit", default=5, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def ede_decision(company_uid: str, repo: str, limit: int, as_json: bool) -> None:
    """Full executive decision package (all engines combined)."""
    db, ede = _get_ede(repo)
    result = ede.executive_decision(company_uid, opportunity_limit=limit)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        co_name = result.get("company_name", company_uid)
        conf = result.get("confidence", 0)
        console.print(f"\n[bold cyan]Executive Decision — {co_name}[/bold cyan]  Confidence: [bold]{conf:.0%}[/bold]\n")
        for line in result.get("executive_narrative", []):
            console.print(f"  {line}")
        prios = result.get("strategic_priorities", {}).get("priorities", [])
        if prios:
            t = Table(title="Strategic Priorities", show_lines=False)
            t.add_column("#")
            t.add_column("Level")
            t.add_column("Reason")
            t.add_column("Actions")
            for i, p in enumerate(prios[:5], 1):
                color = "red" if p["level"] == "critical" else ("yellow" if p["level"] == "high" else "green")
                t.add_row(
                    str(i),
                    f"[{color}]{p['level']}[/{color}]",
                    p["reason"][:70],
                    "; ".join(p.get("actions", [])[:2])[:80],
                )
            console.print(t)
        actions = result.get("immediate_actions", [])
        if actions:
            console.print("\n[bold yellow]Immediate Actions:[/bold yellow]")
            for a in actions:
                console.print(f"  → {a}")
    db.close()


@main.command("ede-situation")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def ede_situation(company_uid: str, repo: str, as_json: bool) -> None:
    """Situational awareness: summary, win rate, trend, top buyers."""
    db, ede = _get_ede(repo)
    result = ede.company_situation(company_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(
            f"\n[bold]{result.get('company_name', company_uid)}[/bold]  "
            f"Health: [bold]{result.get('health_score', 0):.0%}[/bold]  "
            f"Win rate: {result.get('win_rate') or 0:.0%}  "
            f"Trend: {result.get('trend_label', 'unknown')}"
        )
        buyers = result.get("top_buyers", [])
        if buyers:
            t = Table(title="Top Buyers", show_lines=False)
            t.add_column("Buyer")
            for b in buyers[:5]:
                t.add_row(b.get("name", b.get("uid", "")))
            console.print(t)
    db.close()


@main.command("ede-market")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def ede_market(company_uid: str, repo: str, as_json: bool) -> None:
    """Market position: competitive pressure, classification, rivals."""
    db, ede = _get_ede(repo)
    result = ede.market_position(company_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        console.print(
            f"\n[bold]Market Position[/bold]  "
            f"Classification: [bold cyan]{result.get('classification', 'unknown')}[/bold cyan]  "
            f"Pressure: {result.get('pressure_score', 0):.2f} "
            f"({result.get('pressure_level', '')})"
        )
        rivals = result.get("direct_competitors", [])
        if rivals:
            t = Table(title="Direct Competitors", show_lines=False)
            t.add_column("Name")
            t.add_column("Co-bids")
            for r in rivals[:5]:
                t.add_row(
                    r.get("name", r.get("uid", "")),
                    str(r.get("co_bid_count", r.get("count", ""))),
                )
            console.print(t)
    db.close()


@main.command("ede-priorities")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def ede_priorities(company_uid: str, repo: str, as_json: bool) -> None:
    """Ranked strategic priorities with recommended actions."""
    db, ede = _get_ede(repo)
    result = ede.strategic_priorities(company_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        prios = result.get("priorities", [])
        t = Table(title=f"Strategic Priorities ({len(prios)} total)", show_lines=False)
        t.add_column("#")
        t.add_column("Level")
        t.add_column("Reason")
        t.add_column("Actions")
        for i, p in enumerate(prios[:8], 1):
            color = "red" if p["level"] == "critical" else ("yellow" if p["level"] == "high" else "green")
            t.add_row(
                str(i),
                f"[{color}]{p['level']}[/{color}]",
                p["reason"][:70],
                "; ".join(p.get("actions", [])[:1])[:80],
            )
        console.print(t)
    db.close()


@main.command("ede-risks")
@click.argument("company_uid")
@click.option("--repo", default=".", show_default=True)
@click.option("--json", "as_json", is_flag=True)
def ede_risks(company_uid: str, repo: str, as_json: bool) -> None:
    """Consolidated risk register from all engines."""
    db, ede = _get_ede(repo)
    result = ede.risk_register(company_uid)
    if as_json or "error" in result:
        click.echo(json.dumps(result, indent=2))
    else:
        overall = result.get("overall_risk", "unknown")
        color = "red" if overall == "high" else ("yellow" if overall == "medium" else "green")
        console.print(
            f"\n[bold]Risk Register[/bold]  Overall: [{color}]{overall}[/{color}]  "
            f"({result.get('risk_count', 0)} risks)"
        )
        t = Table(show_lines=False)
        t.add_column("Source")
        t.add_column("Factor")
        t.add_column("Sev")
        t.add_column("Detail")
        t.add_column("Mitigation")
        for r in result.get("risks", []):
            sev_color = "red" if r["severity"] == "high" else ("yellow" if r["severity"] == "medium" else "green")
            t.add_row(
                r["source"],
                r["factor"],
                f"[{sev_color}]{r['severity']}[/{sev_color}]",
                r["detail"][:60],
                r["mitigation"][:60],
            )
        console.print(t)
    db.close()
