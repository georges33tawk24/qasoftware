"""One-to-one assignment — the Hungarian algorithm.

Greedy assignment picks the best pair, then the best remaining, and so on, which goes
wrong exactly where it hurts most: two similar cards where the greedy first pick forces
the second into the wrong partner. Within a container the matrices are small, so the
optimal answer is affordable and there is no reason to accept the approximate one.
"""

from __future__ import annotations

INF = float("inf")


def solve(cost: list[list[float]]) -> list[tuple[int, int]]:
    """Minimum-cost one-to-one assignment over a rectangular matrix.

    Returns (row, column) pairs. Rows beyond the number of columns go unassigned, which is
    exactly what an unmatched node or element is.
    """
    if not cost or not cost[0]:
        return []

    rows, columns = len(cost), len(cost[0])
    transposed = rows > columns
    if transposed:
        cost = [[cost[r][c] for r in range(rows)] for c in range(columns)]
        rows, columns = columns, rows

    # Jonker-Volgenant style shortest augmenting path with potentials, O(n²m).
    u = [0.0] * (rows + 1)
    v = [0.0] * (columns + 1)
    match = [0] * (columns + 1)
    way = [0] * (columns + 1)

    for row in range(1, rows + 1):
        match[0] = row
        free = 0
        minimum = [INF] * (columns + 1)
        used = [False] * (columns + 1)
        while True:
            used[free] = True
            current, delta, next_free = match[free], INF, 0
            for column in range(1, columns + 1):
                if used[column]:
                    continue
                reduced = cost[current - 1][column - 1] - u[current] - v[column]
                if reduced < minimum[column]:
                    minimum[column] = reduced
                    way[column] = free
                if minimum[column] < delta:
                    delta = minimum[column]
                    next_free = column
            for column in range(columns + 1):
                if used[column]:
                    u[match[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            free = next_free
            if match[free] == 0:
                break
        while free:
            previous = way[free]
            match[free] = match[previous]
            free = previous

    pairs = [(match[column] - 1, column - 1) for column in range(1, columns + 1) if match[column]]
    return [(c, r) for r, c in pairs] if transposed else pairs
