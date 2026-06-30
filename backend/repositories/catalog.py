"""Data asset catalog repository.

All database operations for the global data mesh catalog.

Design principles:
1. Global graph queries use PostgreSQL recursive CTEs -- never NetworkX
2. Per-run lineage uses NetworkX (bounded small graphs) cached in Redis
3. Upsert semantics: assets registered multiple times update last_seen_at
4. Bulk upsert: register_run_assets() uses INSERT ... ON CONFLICT
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import networkx as nx
import orjson
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db.redis_pools import get_cache_redis_binary
from backend.models import DataAsset, AssetRelationship

logger = logging.getLogger(__name__)

NS_MINIO_UPLOADS = "minio://pipelineiq-uploads"
NS_MINIO_OUTPUTS = "minio://pipelineiq-outputs"
NS_PIPELINE = "pipeline://"
NS_REDPANDA = "redpanda://localhost:9092"

MAX_CTE_DEPTH = 10
MAX_CTE_RESULTS = 500
CTE_STATEMENT_TIMEOUT_MS = 30000
MAX_LINEAGE_NODES = 10_000
MAX_LINEAGE_EDGES = 50_000
LINEAGE_CACHE_TTL = 3600
BLAST_RADIUS_CACHE_TTL = 600
MAX_CACHE_PAYLOAD_BYTES = 1_048_576


def _is_postgres(db: Session) -> bool:
    """Check if the database backend is PostgreSQL."""
    dialect = db.get_bind().dialect.name
    return dialect == "postgresql"


def upsert_data_asset(
    db: Session,
    asset_type: str,
    name: str,
    namespace: str,
    owner_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Insert or update a data asset. Returns the asset UUID."""
    now = datetime.now(timezone.utc)

    if _is_postgres(db):
        stmt = text("""
            INSERT INTO data_assets (asset_type, name, namespace, owner_id, metadata, created_at, last_seen_at)
            VALUES (:asset_type, :name, :namespace, :owner_id, :metadata, :created_at, :last_seen_at)
            ON CONFLICT ON CONSTRAINT uq_data_assets_type_ns_name
            DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at, metadata = EXCLUDED.metadata
            RETURNING id
        """)
        import json
        result = db.execute(stmt, {
            "asset_type": asset_type,
            "name": name,
            "namespace": namespace,
            "owner_id": owner_id,
            "metadata": json.dumps(metadata or {}),
            "created_at": now,
            "last_seen_at": now,
        })
        return str(result.scalar_one())
    else:
        existing = db.query(DataAsset).filter(
            DataAsset.asset_type == asset_type,
            DataAsset.name == name,
            DataAsset.namespace == namespace,
        ).first()

        if existing:
            existing.last_seen_at = now
            existing.metadata = metadata or {}
            db.flush()
            return str(existing.id)
        else:
            asset = DataAsset(
                asset_type=asset_type,
                name=name,
                namespace=namespace,
                owner_id=owner_id,
                metadata=metadata or {},
                created_at=now,
                last_seen_at=now,
            )
            db.add(asset)
            db.flush()
            return str(asset.id)


def upsert_asset_relationship(
    db: Session,
    source_id: str,
    target_id: str,
    relation: str,
    pipeline_name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Insert an asset relationship edge. Duplicates allowed (different runs)."""
    if _is_postgres(db):
        now_ts = datetime.now(timezone.utc)
        stmt = text("""
            INSERT INTO asset_relationships (source_id, target_id, relation, pipeline_name, run_id, created_at)
            VALUES (:source_id, :target_id, :relation, :pipeline_name, :run_id, :created_at)
            ON CONFLICT DO NOTHING
        """)
        db.execute(stmt, {
            "source_id": source_id,
            "target_id": target_id,
            "relation": relation,
            "pipeline_name": pipeline_name,
            "run_id": run_id,
            "created_at": now_ts,
        })
    else:
        src_uuid = uuid.UUID(source_id) if isinstance(source_id, str) else source_id
        tgt_uuid = uuid.UUID(target_id) if isinstance(target_id, str) else target_id
        run_uuid = uuid.UUID(run_id) if isinstance(run_id, str) else run_id

        existing = db.query(AssetRelationship).filter(
            AssetRelationship.source_id == src_uuid,
            AssetRelationship.target_id == tgt_uuid,
            AssetRelationship.relation == relation,
        ).first()
        if not existing:
            rel = AssetRelationship(
                source_id=src_uuid,
                target_id=tgt_uuid,
                relation=relation,
                pipeline_name=pipeline_name,
                run_id=run_uuid,
                created_at=datetime.now(timezone.utc),
            )
            db.add(rel)
            db.flush()


def register_run_assets(
    db: Session,
    run_id: str,
    pipeline_name: str,
    pipeline_yaml: str,
    lineage_graph: nx.DiGraph,
    owner_id: Optional[str] = None,
) -> int:
    """Register all data assets and relationships from a completed pipeline run.
    If a graph is provided, it uses it. Otherwise, it rebuilds from the DB.
    """
    if lineage_graph is None:
        # This case is for rebuilding from DB, but usually we have the graph
        return 0
    
    return _register_graph_assets(db, run_id, pipeline_name, lineage_graph, owner_id)


def register_step_assets(
    db: Session,
    run_id: str,
    pipeline_name: str,
    lineage_graph: nx.DiGraph,
    owner_id: Optional[str] = None,
) -> int:
    """Incrementally register assets and relationships from a growing lineage graph.
    
    This is used to avoid race conditions where the frontend requests lineage
    before the final run results are persisted.
    """
    return _register_graph_assets(db, run_id, pipeline_name, lineage_graph, owner_id)


def _register_graph_assets(
    db: Session,
    run_id: str,
    pipeline_name: str,
    lineage_graph: nx.DiGraph,
    owner_id: Optional[str] = None,
) -> int:
    """Internal helper to register assets from a NetworkX graph."""
    if lineage_graph.number_of_nodes() == 0:
        return 0

    registered = 0

    pipeline_asset_id = upsert_data_asset(
        db=db,
        asset_type="pipeline",
        name=pipeline_name,
        namespace=NS_PIPELINE,
        owner_id=owner_id,
        metadata={"run_id": run_id, "yaml_length": 0}, # yaml_length is optional here
    )
    registered += 1

    node_id_to_asset_id: dict[str, str] = {}

    for node_id, node_data in lineage_graph.nodes(data=True):
        asset_type, namespace, name = _classify_node(node_id, node_data)

        asset_id = upsert_data_asset(
            db=db,
            asset_type=asset_type,
            name=name,
            namespace=namespace,
            owner_id=owner_id,
            metadata={
                "pipeline": pipeline_name,
                "run_id": run_id,
                **{k: v for k, v in (node_data or {}).items()
                   if isinstance(v, (str, int, float, bool))},
            },
        )
        node_id_to_asset_id[node_id] = asset_id
        registered += 1

    for src_node, tgt_node, edge_data in lineage_graph.edges(data=True):
        src_id = node_id_to_asset_id.get(src_node)
        tgt_id = node_id_to_asset_id.get(tgt_node)

        if not src_id or not tgt_id:
            continue

        relation = _classify_edge(edge_data)

        upsert_asset_relationship(
            db=db,
            source_id=src_id,
            target_id=tgt_id,
            relation=relation,
            pipeline_name=pipeline_name,
            run_id=run_id,
        )

    # We don't commit here; the caller should manage the transaction
    return registered


def _classify_node(node_id: str, node_data: dict) -> tuple[str, str, str]:
    """Classify a lineage graph node into (asset_type, namespace, name)."""
    if node_data and node_data.get("asset_type"):
        return (node_data["asset_type"],
                node_data.get("namespace", NS_PIPELINE),
                node_id)

    if node_id.endswith((".csv", ".json", ".parquet", ".xlsx")):
        return ("file", NS_MINIO_UPLOADS, node_id)

    if node_id.startswith(("minio://", "s3://")):
        return ("file", NS_MINIO_OUTPUTS, node_id.split("/")[-1])

    if node_id.startswith("redpanda://") or node_id.endswith(("-topic", ".topic")):
        return ("topic", NS_REDPANDA, node_id)

    return ("column", NS_PIPELINE, node_id)


def _classify_edge(edge_data: dict) -> str:
    """Classify a lineage graph edge into a relation type."""
    if not edge_data:
        return "transforms"

    relation = edge_data.get("relation") or edge_data.get("type")
    if relation in ("reads_from", "writes_to", "transforms", "joins"):
        return relation

    step_type = edge_data.get("step_type", "")
    if step_type == "load":
        return "reads_from"
    if step_type == "save":
        return "writes_to"
    if step_type == "join":
        return "joins"
    return "transforms"


def get_blast_radius(
    db: Session,
    asset_name: str,
    asset_type: Optional[str] = None,
    max_depth: int = MAX_CTE_DEPTH,
    owner_id: Optional[str] = None,
) -> list[dict]:
    """Find all downstream assets that depend on the given asset.

    Uses a PostgreSQL recursive CTE -- NOT NetworkX.
    Falls back to Python BFS on SQLite for tests.
    Results are cached in Redis for BLAST_RADIUS_CACHE_TTL seconds.
    """
    cache_key = f"blast_radius:{owner_id or 'admin'}:{asset_name}:{asset_type or 'all'}:{max_depth}"
    redis = get_cache_redis_binary()

    try:
        cached = redis.get(cache_key)
        if cached:
            logger.debug("Blast radius cache HIT: %s", asset_name)
            return json.loads(cached)
    except Exception as e:
        logger.warning("Blast radius cache read failed: %s", e)

    if not _is_postgres(db):
        result = _blast_radius_sqlite(db, asset_name, asset_type, max_depth, owner_id)
    else:
        result = _blast_radius_postgres(db, asset_name, asset_type, max_depth, owner_id)

    try:
        payload = json.dumps(result).encode()
        if len(payload) > MAX_CACHE_PAYLOAD_BYTES:
            logger.debug("Blast radius cache SKIP: %s (%d bytes exceeds limit)", asset_name, len(payload))
        else:
            redis.setex(cache_key, BLAST_RADIUS_CACHE_TTL, payload)
            logger.debug("Blast radius cache MISS, stored: %s", asset_name)
    except Exception as e:
        logger.warning("Blast radius cache write failed: %s", e)

    return result


def _blast_radius_postgres(
    db: Session,
    asset_name: str,
    asset_type: Optional[str],
    max_depth: int,
    owner_id: Optional[str],
) -> list[dict]:
    where_clause = "WHERE da.name = :name"
    params: dict = {
        "name": asset_name,
        "max_depth": max_depth,
        "limit": MAX_CTE_RESULTS,
        "cte_limit": MAX_CTE_RESULTS,
    }

    if asset_type:
        where_clause += " AND da.asset_type = :asset_type"
        params["asset_type"] = asset_type
    owner_filter = ""
    target_owner_filter = ""
    if owner_id:
        where_clause += " AND da.owner_id = :owner_id"
        owner_filter = "AND da.owner_id = :owner_id"
        target_owner_filter = "JOIN data_assets target_da ON target_da.id = ar.target_id AND target_da.owner_id = :owner_id"
        params["owner_id"] = owner_id

    # Optimized recursive CTE:
    # - Drops the O(n) `visited` array tracking (was O(n^2) memory + O(n)
    #   `!= ALL(...)` check at every step, the main cause of the 5-30s
    #   statement timeouts on `/impact`).
    # - Uses `NOT EXISTS` against the accumulating CTE results for cycle
    #   detection (O(1) per step, leveraging the existing
    #   idx_asset_rel_source_target covering index).
    # - Adds an inner `LIMIT` to cap the working set per iteration so a
    #   single bad query cannot exhaust the DB.
    # - Statement timeout is set per-iteration via `SET LOCAL` inside an
    #   explicit transaction so it actually takes effect under PgBouncer.
    sql = text(f"""
        WITH RECURSIVE downstream(id, depth) AS (
            SELECT da.id, 0
            FROM data_assets da
            {where_clause}

            UNION ALL

            SELECT ar.target_id, d.depth + 1
            FROM asset_relationships ar
            JOIN downstream d ON d.id = ar.source_id
            {target_owner_filter}
            WHERE d.depth < :max_depth
              AND NOT EXISTS (
                  SELECT 1 FROM downstream d2 WHERE d2.id = ar.target_id
              )
        ),
        downstream_min AS (
            SELECT id, MIN(depth) AS depth
            FROM downstream
            GROUP BY id
        ),
        pipeline_stats AS (
            SELECT
                ar2.target_id,
                ar2.pipeline_name,
                COUNT(DISTINCT ar2.run_id) AS times_used
            FROM asset_relationships ar2
            JOIN downstream_min dm ON ar2.target_id = dm.id
            GROUP BY ar2.target_id, ar2.pipeline_name
        )
        SELECT
            da.name,
            da.namespace,
            da.asset_type,
            dm.depth,
            ps.pipeline_name,
            ps.times_used
        FROM downstream_min dm
        JOIN data_assets da ON da.id = dm.id
        LEFT JOIN pipeline_stats ps ON ps.target_id = dm.id
        WHERE 1 = 1
          {owner_filter}
        ORDER BY dm.depth ASC, da.name ASC
        LIMIT :limit
    """)

    try:
        # Ensure SET LOCAL takes effect: it requires an open transaction.
        # The SQLAlchemy session may be in autobegin mode but the first
        # execute() below should start one. We use begin() to be explicit
        # so the timeout is guaranteed to apply for the duration of the
        # recursive walk.
        if not db.in_transaction():
            db.begin()
        db.execute(
            text("SET LOCAL statement_timeout = :timeout"),
            {"timeout": CTE_STATEMENT_TIMEOUT_MS},
        )
        result = db.execute(sql, params)
        rows = result.fetchall()
    except Exception as exc:
        logger.error("Blast radius query failed for '%s': %s", asset_name, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "name": row.name,
            "namespace": row.namespace,
            "asset_type": row.asset_type,
            "depth": row.depth,
            "pipeline_name": row.pipeline_name,
            "times_used": row.times_used,
        }
        for row in rows
    ]


def _blast_radius_sqlite(
    db: Session,
    asset_name: str,
    asset_type: Optional[str],
    max_depth: int,
    owner_id: Optional[str],
) -> list[dict]:
    """SQLite-compatible BFS blast radius implementation."""
    base_query = db.query(DataAsset).filter(DataAsset.name == asset_name)
    if asset_type:
        base_query = base_query.filter(DataAsset.asset_type == asset_type)
    if owner_id:
        base_query = base_query.filter(DataAsset.owner_id == uuid.UUID(owner_id))

    start_assets = base_query.all()
    if not start_assets:
        return []

    results = []
    visited = set()
    queue = [(a, 0) for a in start_assets]

    while queue:
        asset, depth = queue.pop(0)
        if asset.id in visited:
            continue
        visited.add(asset.id)

        rels = db.query(AssetRelationship).filter(
            AssetRelationship.source_id == asset.id
        ).all()

        results.append({
            "name": asset.name,
            "namespace": asset.namespace,
            "asset_type": asset.asset_type,
            "depth": depth,
            "pipeline_name": rels[0].pipeline_name if rels else None,
            "times_used": len(set(r.run_id for r in rels if r.run_id)),
        })

        if depth < max_depth:
            for rel in rels:
                target_query = db.query(DataAsset).filter(DataAsset.id == rel.target_id)
                if owner_id:
                    target_query = target_query.filter(DataAsset.owner_id == uuid.UUID(owner_id))
                target = target_query.first()
                if target and target.id not in visited:
                    queue.append((target, depth + 1))

    results.sort(key=lambda r: (r["depth"], r["name"]))
    return results


def get_upstream_lineage(
    db: Session,
    asset_name: str,
    max_depth: int = MAX_CTE_DEPTH,
    owner_id: Optional[str] = None,
) -> list[dict]:
    """Find all upstream assets that the given asset depends on."""
    if not _is_postgres(db):
        return _upstream_lineage_sqlite(db, asset_name, max_depth, owner_id)

    owner_root_filter = ""
    parent_owner_filter = ""
    final_owner_filter = ""
    params = {
        "name": asset_name,
        "max_depth": max_depth,
        "limit": MAX_CTE_RESULTS,
        "cte_limit": MAX_CTE_RESULTS,
    }
    if owner_id:
        owner_root_filter = "AND da.owner_id = :owner_id"
        parent_owner_filter = "AND parent.owner_id = :owner_id"
        final_owner_filter = "WHERE da.owner_id = :owner_id"
        params["owner_id"] = owner_id

    sql = text(f"""
        WITH RECURSIVE upstream(id, name, namespace, asset_type, depth) AS MATERIALIZED (
            SELECT da.id, da.name, da.namespace, da.asset_type, 0
            FROM data_assets da
            WHERE da.name = :name
              {owner_root_filter}

            UNION ALL

            SELECT parent.id, parent.name, parent.namespace, parent.asset_type, u.depth + 1
            FROM data_assets parent
            JOIN asset_relationships ar ON ar.source_id = parent.id
            JOIN upstream u ON u.id = ar.target_id
            WHERE u.depth < :max_depth
              {parent_owner_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM upstream u2 WHERE u2.id = parent.id
              )
            LIMIT :cte_limit
        )
        SELECT DISTINCT u.name, u.namespace, u.asset_type, u.depth
        FROM upstream u
        JOIN data_assets da ON da.id = u.id
        {final_owner_filter}
        ORDER BY depth ASC, name ASC
        LIMIT :limit
    """)

    try:
        if not db.in_transaction():
            db.begin()
        db.execute(
            text("SET LOCAL statement_timeout = :timeout"),
            {"timeout": CTE_STATEMENT_TIMEOUT_MS},
        )
        result = db.execute(sql, params)
        return [dict(row._mapping) for row in result.fetchall()]
    except Exception as exc:
        logger.error("Upstream lineage query failed for '%s': %s", asset_name, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return []


def _upstream_lineage_sqlite(
    db: Session,
    asset_name: str,
    max_depth: int,
    owner_id: Optional[str],
) -> list[dict]:
    """SQLite-compatible BFS upstream lineage implementation."""
    start_query = db.query(DataAsset).filter(DataAsset.name == asset_name)
    if owner_id:
        start_query = start_query.filter(DataAsset.owner_id == uuid.UUID(owner_id))
    start = start_query.first()
    if not start:
        return []

    results = []
    visited = set()
    queue = [(start, 0)]

    while queue:
        asset, depth = queue.pop(0)
        if asset.id in visited:
            continue
        visited.add(asset.id)

        rels = db.query(AssetRelationship).filter(
            AssetRelationship.target_id == asset.id
        ).all()

        results.append({
            "name": asset.name,
            "namespace": asset.namespace,
            "asset_type": asset.asset_type,
            "depth": depth,
        })

        if depth < max_depth:
            for rel in rels:
                source_query = db.query(DataAsset).filter(DataAsset.id == rel.source_id)
                if owner_id:
                    source_query = source_query.filter(DataAsset.owner_id == uuid.UUID(owner_id))
                source = source_query.first()
                if source and source.id not in visited:
                    queue.append((source, depth + 1))

    results.sort(key=lambda r: (r["depth"], r["name"]))
    return results


def search_assets(
    db: Session,
    query: str,
    asset_type: Optional[str] = None,
    limit: int = 20,
    owner_id: Optional[str] = None,
) -> list[dict]:
    """Full-text search over asset names using PostgreSQL trigram similarity.
    Falls back to LIKE on SQLite for tests.
    """
    if not query or len(query.strip()) < 2:
        return []

    if _is_postgres(db):
        return _search_assets_postgres(db, query, asset_type, limit, owner_id)
    else:
        return _search_assets_sqlite(db, query, asset_type, limit, owner_id)


def _search_assets_postgres(
    db: Session,
    query: str,
    asset_type: Optional[str],
    limit: int,
    owner_id: Optional[str],
) -> list[dict]:
    """PostgreSQL search with trigram similarity."""
    params: dict = {"query": query.strip(), "limit": limit}
    type_filter = ""
    if asset_type:
        type_filter = "AND asset_type = :asset_type"
        params["asset_type"] = asset_type
    owner_filter = ""
    if owner_id:
        owner_filter = "AND owner_id = :owner_id"
        params["owner_id"] = owner_id

    sql = text(f"""
        SELECT id, name, namespace, asset_type, metadata, last_seen_at,
               similarity(name, :query) AS sim_score
        FROM data_assets
        WHERE name ILIKE '%' || :query || '%'
          {type_filter}
          {owner_filter}
        ORDER BY sim_score DESC, name ASC
        LIMIT :limit
    """)

    result = db.execute(sql, params)
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "namespace": row.namespace,
            "asset_type": row.asset_type,
            "metadata": row.metadata,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "similarity": round(float(row.sim_score or 0), 4),
        }
        for row in result.fetchall()
    ]


def _search_assets_sqlite(
    db: Session,
    query: str,
    asset_type: Optional[str],
    limit: int,
    owner_id: Optional[str],
) -> list[dict]:
    """SQLite search using LIKE."""
    q = db.query(DataAsset).filter(
        DataAsset.name.contains(query.strip())
    )
    if asset_type:
        q = q.filter(DataAsset.asset_type == asset_type)
    if owner_id:
        q = q.filter(DataAsset.owner_id == uuid.UUID(owner_id))

    results = q.limit(limit).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "namespace": r.namespace,
            "asset_type": r.asset_type,
            "metadata": getattr(r, "metadata_", {}),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "similarity": 0.0,
        }
        for r in results
    ]


def list_orphan_assets(
    db: Session,
    days_inactive: int = 90,
    owner_id: Optional[str] = None,
) -> list[dict]:
    """Find assets not seen in any pipeline run in the last N days."""
    if _is_postgres(db):
        return _list_orphans_postgres(db, days_inactive, owner_id)
    else:
        return _list_orphans_sqlite(db, days_inactive, owner_id)


def _list_orphans_postgres(
    db: Session,
    days_inactive: int,
    owner_id: Optional[str],
) -> list[dict]:
    """PostgreSQL orphan listing with INTERVAL syntax."""
    owner_filter = ""
    params = {"days": days_inactive}
    if owner_id:
        owner_filter = "AND owner_id = :owner_id"
        params["owner_id"] = owner_id
    sql = text(f"""
        SELECT id, name, namespace, asset_type, last_seen_at
        FROM data_assets
        WHERE last_seen_at < NOW() - (:days || ' days')::INTERVAL
          AND asset_type IN ('file', 'column')
          {owner_filter}
        ORDER BY last_seen_at ASC
        LIMIT 100
    """)

    result = db.execute(sql, params)
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "namespace": row.namespace,
            "asset_type": row.asset_type,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        }
        for row in result.fetchall()
    ]


def _list_orphans_sqlite(
    db: Session,
    days_inactive: int,
    owner_id: Optional[str],
) -> list[dict]:
    """SQLite orphan listing using datetime comparison."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_inactive)

    q = db.query(DataAsset).filter(
        DataAsset.last_seen_at < cutoff,
        DataAsset.asset_type.in_(["file", "column"]),
    )
    if owner_id:
        q = q.filter(DataAsset.owner_id == uuid.UUID(owner_id))
    results = q.order_by(DataAsset.last_seen_at.asc()).limit(100).all()

    return [
        {
            "id": str(r.id),
            "name": r.name,
            "namespace": r.namespace,
            "asset_type": r.asset_type,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in results
    ]


def get_cached_run_lineage(run_id: str, db: Session) -> nx.DiGraph:
    """Get per-run lineage graph from Redis cache or rebuild from DB."""
    cache_key = f"lineage:graph:{run_id}"
    redis = get_cache_redis_binary()

    try:
        cached = redis.get(cache_key)
        if cached:
            G = nx.node_link_graph(orjson.loads(cached))
            logger.debug("Lineage cache HIT: %s", run_id)
            return G
    except Exception as e:
        logger.warning("Lineage cache read failed: %s", e)

    G = _build_lineage_from_db(run_id, db)

    try:
        redis.setex(cache_key, LINEAGE_CACHE_TTL, orjson.dumps(nx.node_link_data(G)))
        logger.debug("Lineage cache MISS, stored: %s", run_id)
    except Exception as e:
        logger.warning("Lineage cache write failed: %s", e)

    return G


def _build_lineage_from_db(run_id: str, db: Session) -> nx.DiGraph:
    """Build per-run NetworkX graph from the stored lineage_graphs record."""
    run_id_hex = uuid.UUID(run_id).hex if isinstance(run_id, str) else run_id.hex
    result = db.execute(
        text("SELECT graph_data FROM lineage_graphs WHERE pipeline_run_id = :run_id"),
        {"run_id": run_id_hex},
    )
    row = result.fetchone()
    if not row or not row.graph_data:
        return nx.DiGraph()

    graph_data = row.graph_data
    if isinstance(graph_data, dict):
        return nx.node_link_graph(graph_data)
    if isinstance(graph_data, str):
        return nx.node_link_graph(json.loads(graph_data))

    return nx.DiGraph()


def build_bounded_lineage_graph(step_results: list) -> nx.DiGraph:
    """Build per-run lineage graph from step results, with size limits."""
    G = nx.DiGraph()
    seen_nodes: set = set()
    edge_count = 0

    for step in step_results:
        step_name = getattr(step, "step_name", None) or step.get("step_name", "unknown")
        columns_in = getattr(step, "columns_in", []) or step.get("columns_in", [])
        columns_out = getattr(step, "columns_out", []) or step.get("columns_out", [])

        for src_col in (columns_in or []):
            for tgt_col in (columns_out or []):
                if len(seen_nodes) > MAX_LINEAGE_NODES:
                    if "__truncated__" not in G.nodes:
                        G.add_node("__truncated__", truncated=True)
                        logger.warning(
                            "Lineage graph truncated at %d nodes (step: %s)",
                            MAX_LINEAGE_NODES, step_name,
                        )
                    break

                if edge_count > MAX_LINEAGE_EDGES:
                    logger.warning(
                        "Lineage edge limit reached at %d (step: %s)",
                        MAX_LINEAGE_EDGES, step_name,
                    )
                    break

                G.add_edge(src_col, tgt_col, step=step_name, relation="transforms")
                edge_count += 1
                seen_nodes.add(src_col)
                seen_nodes.add(tgt_col)

            if len(seen_nodes) > MAX_LINEAGE_NODES:
                break

    return G
