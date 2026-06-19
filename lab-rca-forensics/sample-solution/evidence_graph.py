#!/usr/bin/env python3
"""Render one causal-chain graph per identified incident.

Writes a single PNG with five subplots: evidence_graph.png.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx

OUT = Path(__file__).parent / "evidence_graph.png"

ROOT_COLOR = "#d62728"
SVC_COLOR = "#1f77b4"
EFFECT_COLOR = "#bbbbbb"


def _draw(ax, title: str, edges: list[tuple[str, str, str]],
          root_nodes: set[str], effect_nodes: set[str]) -> None:
    g = nx.DiGraph()
    for src, dst, lbl in edges:
        g.add_edge(src, dst, label=lbl)
    pos = nx.spring_layout(g, seed=11, k=1.6, iterations=80)
    colors = []
    for n in g.nodes():
        if n in root_nodes:
            colors.append(ROOT_COLOR)
        elif n in effect_nodes:
            colors.append(EFFECT_COLOR)
        else:
            colors.append(SVC_COLOR)
    nx.draw_networkx_nodes(g, pos, ax=ax, node_color=colors,
                           node_size=1400, alpha=0.9)
    nx.draw_networkx_labels(g, pos, ax=ax, font_size=6,
                            font_color="white")
    nx.draw_networkx_edges(g, pos, ax=ax, arrows=True, arrowsize=12,
                           edge_color="#444444", width=1.1,
                           connectionstyle="arc3,rad=0.10")
    edge_labels = nx.get_edge_attributes(g, "label")
    nx.draw_networkx_edge_labels(g, pos, ax=ax, edge_labels=edge_labels,
                                 font_size=5, label_pos=0.5)
    ax.set_title(title, fontsize=10)
    ax.axis("off")


INCIDENTS = [
    {
        "title": "I-1 — fx-api retry storm",
        "edges": [
            ("fx-api (3rd-party)", "payment-svc.fx_client", "503 burst"),
            ("payment-svc.fx_client", "payment-svc.http_client",
             "3x retries"),
            ("payment-svc.http_client", "payment-svc",
             "conn pool saturation"),
            ("payment-svc", "rds-orders",
             "audit_log INSERT amplification"),
            ("rds-orders", "rds-orders.cpu", "CPU climb (effect)"),
            ("payment-svc", "payment-svc.memory",
             "held buffers (effect)"),
            ("payment-svc", "checkout-svc.payment_client", "5xx + timeout"),
            ("checkout-svc.payment_client", "checkout-svc",
             "circuit open"),
            ("checkout-svc", "redis-cache.hit_rate",
             "queue backlog (effect)"),
            ("checkout-svc", "frontend", "5xx propagated"),
        ],
        "root": {"fx-api (3rd-party)"},
        "effect": {"rds-orders.cpu", "payment-svc.memory",
                   "redis-cache.hit_rate"},
    },
    {
        "title": "I-2 — inventory cache memory leak",
        "edges": [
            ("nightly_sku_sync (02:00)", "inventory-svc.response_cache",
             "load large SKUs"),
            ("inventory-svc.response_cache", "inventory-svc",
             "5MB entries, ttl=null"),
            ("inventory-svc", "inventory-svc.memory",
             "heap grows monotonically"),
            ("inventory-svc.memory", "inventory-svc.gc",
             "GC pressure"),
            ("inventory-svc.memory", "payment-svc.gc",
             "noisy neighbor (effect)"),
            ("inventory-svc", "inventory-svc.oom_kill",
             "OOM at heap > limit"),
            ("inventory-svc.oom_kill", "checkout-svc",
             "downstream 5xx"),
        ],
        "root": {"nightly_sku_sync (02:00)",
                 "inventory-svc.response_cache"},
        "effect": {"payment-svc.gc"},
    },
    {
        "title": "I-3 — feature flag → unindexed query",
        "edges": [
            ("flag: enable_loyalty_recommendations (11:15)",
             "payment-svc.loyalty_client", "rollout 100%"),
            ("payment-svc.loyalty_client", "rds-orders.transactions",
             "SELECT ... WHERE user_id=? no index"),
            ("rds-orders.transactions", "rds-orders.cpu",
             "CPU climb 75% (effect)"),
            ("rds-orders.transactions", "payment-svc.rds_pool",
             "long-running queries hold conns"),
            ("payment-svc.rds_pool", "payment-svc",
             "pool drained (50 max)"),
            ("payment-svc", "checkout-svc.payment_client",
             "downstream_timeout"),
            ("checkout-svc.payment_client", "frontend",
             "5xx propagated"),
        ],
        "root": {"flag: enable_loyalty_recommendations (11:15)"},
        "effect": {"rds-orders.cpu"},
    },
    {
        "title": "I-4 — DNS AZ-c split for pp-api",
        "edges": [
            ("pp-api vendor IP rotation",
             "AZ-c resolver (stale cache)",
             "old block decommissioned 09:00"),
            ("AZ-c resolver (stale cache)", "payment-svc(AZ-c)",
             "resolved_ip=203.0.113.10 (dead)"),
            ("payment-svc(AZ-c)", "pp-api (3rd-party)",
             "TCP connect to dead IP"),
            ("pp-api (3rd-party)", "payment-svc(AZ-c)",
             "connection_refused"),
            ("payment-svc(AZ-c)", "checkout-svc",
             "5xx for AZ-c traffic"),
            ("AZ-a/b resolvers (healthy)", "payment-svc(AZ-a/b)",
             "resolved_ip=198.51.100.x"),
            ("payment-svc(AZ-a/b)", "pp-api (3rd-party)",
             "charge_ok"),
        ],
        "root": {"pp-api vendor IP rotation",
                 "AZ-c resolver (stale cache)"},
        "effect": {"AZ-a/b resolvers (healthy)",
                   "payment-svc(AZ-a/b)"},
    },
    {
        "title": "I-5 — mTLS cert clock skew",
        "edges": [
            ("mesh controller (clock OK)",
             "new cert not_before=06:00:15Z", "rotated at 06:00:00"),
            ("new cert not_before=06:00:15Z",
             "checkout-svc.mtls_client",
             "present to handshake"),
            ("checkout-svc.mtls_client", "payment-svc validator (clock -27s)",
             "handshake"),
            ("payment-svc validator (clock -27s)",
             "TLS reject: not_yet_valid",
             "current=05:59:48 < not_before"),
            ("TLS reject: not_yet_valid", "checkout-svc",
             "5xx → frontend"),
            ("validator clock catches up (06:15)",
             "TLS recovered", "natural NTP convergence"),
        ],
        "root": {"mesh controller (clock OK)",
                 "payment-svc validator (clock -27s)"},
        "effect": {"validator clock catches up (06:15)",
                   "TLS recovered"},
    },
]


def main() -> None:
    fig, axes = plt.subplots(3, 2, figsize=(18, 22))
    flat = axes.flatten()
    for ax, inc in zip(flat, INCIDENTS):
        _draw(ax, inc["title"], inc["edges"], inc["root"], inc["effect"])
    # Hide the unused 6th subplot
    flat[5].axis("off")
    flat[5].text(0.5, 0.5,
                 "red = root cause (real)\ngrey = downstream effect "
                 "or healthy control\nblue = service/component on the "
                 "causal path",
                 ha="center", va="center", fontsize=11)
    plt.tight_layout()
    plt.savefig(OUT, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
