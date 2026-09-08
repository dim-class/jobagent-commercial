/** How the job library's selection reacts to a freshly loaded list.
 *
 * Extracted so the rule can be tested without rendering the page, the same way
 * a task-scoped one was.
 *
 * Two cases, and the difference is the whole point:
 *
 * - a pending "select all" (armed by 筛选并全选未分析岗位 just before it switches
 *   the filter) takes the reloaded result wholesale;
 * - otherwise the selection is pruned to what the list actually returned, so a
 *   filter change can never leave ids selected that are no longer on screen.
 *
 * `current` is returned unchanged when nothing was pruned, so an unrelated
 * refresh does not churn React state.
 */
export function nextJobSelection(
  current: Set<number>,
  visibleIds: Set<number>,
  selectAll: boolean,
): Set<number> {
  if (selectAll) return new Set(visibleIds)
  const next = new Set([...current].filter((id) => visibleIds.has(id)))
  // `next` is built by filtering `current`, so equal sizes means equal sets.
  return next.size === current.size ? current : next
}
