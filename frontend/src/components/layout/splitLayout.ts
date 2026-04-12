export function getMinPanelWidth(total: number) {
  const preferred = Math.round(total * 0.28)
  const softMin = Math.max(260, Math.min(420, preferred))
  const maxFitMin = Math.max(180, Math.floor(total / 2) - 24)
  return Math.min(softMin, maxFitMin)
}
