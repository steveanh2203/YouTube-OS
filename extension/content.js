/**
 * AutoCapCut Bridge — Content Script
 * Inject vào claude.ai, detect text selection, hiện floating button + panel.
 * Flow: bôi đen text → ⚡ button → chọn Parent Project → Child Project → Feature → Gửi
 */

const API_BASE   = 'http://127.0.0.1:8765'
const BTN_ID     = 'acc-floating-btn'
const PANEL_ID   = 'acc-panel'
const OVERLAY_ID = 'acc-overlay'

// ── localStorage keys — lưu phiên làm việc ────────────────────────────────────
const LS_PARENT_ID  = 'acc_session_parent_id'
const LS_CHILD_ID   = 'acc_session_child_id'
const LS_FEATURE_ID = 'acc_session_feature_id'

let selectedText   = ''
let features       = []
let featuresLoaded = false

// ── Bootstrap ──────────────────────────────────────────────────────────────────

function init() {
  injectFloatingBtn()
  injectPanel()
  bindGlobalEvents()
}

// ── Floating Button ────────────────────────────────────────────────────────────

function injectFloatingBtn() {
  if (document.getElementById(BTN_ID)) return

  const btn = document.createElement('button')
  btn.id = BTN_ID
  btn.innerHTML = `
    <span class="acc-btn-icon">⚡</span>
    <span class="acc-btn-label">Gửi vào AutoCapCut</span>
  `
  btn.addEventListener('mousedown', (e) => {
    e.preventDefault()
    e.stopPropagation()
    showPanel()
    hideFloatingBtn()
  })
  document.body.appendChild(btn)
}

function showFloatingBtn(rect) {
  const btn = document.getElementById(BTN_ID)
  if (!btn) return
  const x = Math.min(rect.right + window.scrollX - 180, window.innerWidth - 200)
  const y = rect.bottom + window.scrollY + 10
  btn.style.left = `${x}px`
  btn.style.top  = `${y}px`
  btn.classList.add('acc-visible')
}

function hideFloatingBtn() {
  document.getElementById(BTN_ID)?.classList.remove('acc-visible')
}

// ── Panel HTML ─────────────────────────────────────────────────────────────────

function injectPanel() {
  if (document.getElementById(PANEL_ID)) return

  const overlay = document.createElement('div')
  overlay.id = OVERLAY_ID
  overlay.addEventListener('mousedown', () => { hidePanel(); hideFloatingBtn() })
  document.body.appendChild(overlay)

  const panel = document.createElement('div')
  panel.id = PANEL_ID
  panel.innerHTML = `
    <div class="acc-panel-header">
      <div class="acc-panel-title">
        <span class="acc-panel-logo">⚡</span>
        <span>AutoCapCut Bridge</span>
      </div>
      <button class="acc-panel-close" id="acc-panel-close">✕</button>
    </div>

    <div class="acc-panel-body">

      <div class="acc-preview-wrap">
        <div class="acc-preview-label">Nội dung sẽ gửi</div>
        <div class="acc-preview-text" id="acc-preview"></div>
      </div>

      <div class="acc-fields">

        <div class="acc-field">
          <label class="acc-field-label">Project</label>
          <div class="acc-select-wrap">
            <select class="acc-select" id="acc-parent-select">
              <option value="">⏳ Đang tải projects...</option>
            </select>
            <span class="acc-select-arrow">▾</span>
          </div>
        </div>

        <div class="acc-field">
          <label class="acc-field-label">Video</label>
          <div class="acc-select-wrap">
            <select class="acc-select" id="acc-child-select" disabled>
              <option value="">— Chọn project trước —</option>
            </select>
            <span class="acc-select-arrow">▾</span>
          </div>
        </div>

        <div class="acc-field">
          <label class="acc-field-label">Feature</label>
          <div class="acc-select-wrap">
            <select class="acc-select" id="acc-feature-select">
              <option value="">⏳ Đang tải features...</option>
            </select>
            <span class="acc-select-arrow">▾</span>
          </div>
        </div>

      </div>

    </div>

    <div class="acc-panel-footer">
      <button class="acc-btn-secondary" id="acc-cancel">Huỷ</button>
      <button class="acc-btn-primary" id="acc-send">
        <span id="acc-send-label">⚡ Gửi ngay</span>
      </button>
    </div>
  `
  document.body.appendChild(panel)

  panel.addEventListener('mousedown', (e) => e.stopPropagation())
  document.getElementById('acc-panel-close').addEventListener('click', hidePanel)
  document.getElementById('acc-cancel').addEventListener('click', hidePanel)
  document.getElementById('acc-send').addEventListener('click', sendContent)
  document.getElementById('acc-parent-select').addEventListener('change', onParentChange)
}

// ── Panel Show / Hide ──────────────────────────────────────────────────────────

async function showPanel() {
  const panel   = document.getElementById(PANEL_ID)
  const overlay = document.getElementById(OVERLAY_ID)
  if (!panel || !overlay) return

  // Update preview text
  const preview = document.getElementById('acc-preview')
  const MAX = 200
  preview.textContent = selectedText.length > MAX
    ? selectedText.slice(0, MAX) + '…'
    : selectedText

  // Load features (cached sau lần đầu) + parent projects (có restore session)
  await Promise.all([
    featuresLoaded ? Promise.resolve() : loadFeatures(),
    loadParentProjects(),
  ])

  // Restore feature selection từ localStorage (sau khi options đã load)
  const savedFeatureId = localStorage.getItem(LS_FEATURE_ID)
  if (savedFeatureId) {
    const featureSelect = document.getElementById('acc-feature-select')
    if (featureSelect) featureSelect.value = savedFeatureId
  }

  overlay.classList.add('acc-visible')
  panel.classList.add('acc-visible')
}

function hidePanel() {
  document.getElementById(PANEL_ID)?.classList.remove('acc-visible')
  document.getElementById(OVERLAY_ID)?.classList.remove('acc-visible')
  resetSendBtn()
}

// ── API: Load Parent Projects (có restore session) ─────────────────────────────

async function loadParentProjects() {
  const parentSelect = document.getElementById('acc-parent-select')
  const childSelect  = document.getElementById('acc-child-select')
  if (!parentSelect || !childSelect) return

  parentSelect.disabled = true
  parentSelect.innerHTML = '<option value="">⏳ Đang tải...</option>'
  // Reset child cùng lúc để tránh Bug 3 (child stale từ lần trước)
  childSelect.innerHTML = '<option value="">— Chọn project trước —</option>'
  childSelect.disabled = true

  try {
    const res  = await fetch(`${API_BASE}/api/parent-projects/`, { signal: AbortSignal.timeout(4000) })
    const list = await res.json()

    if (!list.length) {
      parentSelect.innerHTML = '<option value="">⚠️ Chưa có project nào</option>'
      return
    }

    parentSelect.innerHTML =
      '<option value="">— Chọn project —</option>' +
      list.map(p => `<option value="${p.id}">${p.name}</option>`).join('')
    parentSelect.disabled = false

    // Restore phiên: nếu có saved parentId thì tự động chọn lại
    const savedParentId = localStorage.getItem(LS_PARENT_ID)
    if (savedParentId) {
      parentSelect.value = savedParentId
      // Chỉ load children nếu option thực sự tồn tại trong list
      if (parentSelect.value === savedParentId) {
        const savedChildId = localStorage.getItem(LS_CHILD_ID)
        await loadChildProjectsById(savedParentId, savedChildId)
      }
    }
  } catch {
    parentSelect.innerHTML = '<option value="">⚠️ Không kết nối được AutoCapCut</option>'
  }
}

// ── API: Load Child Projects theo parentId ─────────────────────────────────────

async function loadChildProjectsById(parentId, restoreChildId = null) {
  const childSelect = document.getElementById('acc-child-select')
  if (!childSelect) return

  childSelect.disabled = true
  childSelect.innerHTML = '<option value="">⏳ Đang tải videos...</option>'

  try {
    const res  = await fetch(
      `${API_BASE}/api/child-projects/?parent_id=${parentId}&skip=0&limit=100`,
      { signal: AbortSignal.timeout(4000) }
    )
    const list = await res.json()

    if (!list.length) {
      childSelect.innerHTML = '<option value="">⚠️ Chưa có video nào</option>'
      return
    }

    childSelect.innerHTML =
      '<option value="">— Chọn video —</option>' +
      list.map(c => {
        const label = c.title ? `${c.display_name} — ${c.title}` : c.display_name
        return `<option value="${c.id}">${label}</option>`
      }).join('')
    childSelect.disabled = false

    // Restore phiên: chọn lại video đã dùng lần trước
    if (restoreChildId) {
      childSelect.value = restoreChildId
    }
  } catch {
    childSelect.innerHTML = '<option value="">⚠️ Lỗi tải danh sách video</option>'
  }
}

// ── Event: User thay đổi Parent Project ───────────────────────────────────────

async function onParentChange() {
  const parentSelect = document.getElementById('acc-parent-select')
  if (!parentSelect) return

  const parentId = parentSelect.value

  if (!parentId) {
    const childSelect = document.getElementById('acc-child-select')
    if (childSelect) {
      childSelect.innerHTML = '<option value="">— Chọn project trước —</option>'
      childSelect.disabled = true
    }
    localStorage.removeItem(LS_PARENT_ID)
    localStorage.removeItem(LS_CHILD_ID)
    return
  }

  // Lưu parent mới, xoá child cũ (vì parent thay đổi thì child không còn hợp lệ)
  localStorage.setItem(LS_PARENT_ID, parentId)
  localStorage.removeItem(LS_CHILD_ID)

  await loadChildProjectsById(parentId, null)
}

// ── API: Load Features ─────────────────────────────────────────────────────────

async function loadFeatures() {
  const select = document.getElementById('acc-feature-select')
  if (!select) return

  try {
    const res  = await fetch(`${API_BASE}/api/extension/features`, { signal: AbortSignal.timeout(4000) })
    const data = await res.json()
    features       = data.features || []
    featuresLoaded = true

    select.innerHTML = features
      .map(f => `<option value="${f.id}" title="${f.description}">${f.label}</option>`)
      .join('')
  } catch {
    select.innerHTML = '<option value="">⚠️ Không tải được features</option>'
    featuresLoaded = false
  }
}

// ── Send ───────────────────────────────────────────────────────────────────────

async function sendContent() {
  const parentSelect  = document.getElementById('acc-parent-select')
  const childSelect   = document.getElementById('acc-child-select')
  const featureSelect = document.getElementById('acc-feature-select')
  const sendBtn       = document.getElementById('acc-send')
  const sendLabel     = document.getElementById('acc-send-label')
  if (!parentSelect || !childSelect || !featureSelect || !sendBtn || !sendLabel) return

  const parentId  = parentSelect.value
  const childId   = childSelect.value
  const featureId = featureSelect.value

  if (!parentId || !childId || !featureId) {
    shakePanel()
    highlightEmpty([
      !parentId  ? 'acc-parent-select'  : null,
      !childId   ? 'acc-child-select'   : null,
      !featureId ? 'acc-feature-select' : null,
    ])
    return
  }

  sendBtn.disabled      = true
  sendLabel.textContent = '⏳ Đang gửi...'

  try {
    const res = await fetch(`${API_BASE}/api/extension/push`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        feature:           featureId,
        content:           selectedText,
        parent_project_id: parseInt(parentId),
        child_project_id:  parseInt(childId),
      }),
      signal: AbortSignal.timeout(5000),
    })

    if (!res.ok) throw new Error(`HTTP ${res.status}`)

    // Lưu phiên vào localStorage để lần sau restore
    localStorage.setItem(LS_PARENT_ID,  parentId)
    localStorage.setItem(LS_CHILD_ID,   childId)
    localStorage.setItem(LS_FEATURE_ID, featureId)

    sendLabel.textContent = '✅ Đã gửi!'
    sendBtn.classList.add('acc-success')
    setTimeout(() => {
      hidePanel()
      // BUG 1 FIX: ẩn floating button sau khi gửi thành công
      hideFloatingBtn()
      sendBtn.classList.remove('acc-success')
    }, 1400)

  } catch {
    sendLabel.textContent = '❌ Lỗi kết nối'
    sendBtn.disabled = false
    setTimeout(() => { sendLabel.textContent = '⚡ Gửi ngay' }, 2500)
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function resetSendBtn() {
  const sendBtn   = document.getElementById('acc-send')
  const sendLabel = document.getElementById('acc-send-label')
  if (sendBtn && sendLabel) {
    sendBtn.disabled = false
    sendBtn.classList.remove('acc-success')
    sendLabel.textContent = '⚡ Gửi ngay'
  }
}

function shakePanel() {
  const panel = document.getElementById(PANEL_ID)
  if (!panel) return
  panel.classList.add('acc-shake')
  setTimeout(() => panel.classList.remove('acc-shake'), 500)
}

function highlightEmpty(ids) {
  ids.filter(Boolean).forEach(id => {
    const el = document.getElementById(id)
    if (!el) return
    el.classList.add('acc-error')
    setTimeout(() => el.classList.remove('acc-error'), 2000)
  })
}

// ── Global Events ──────────────────────────────────────────────────────────────

function bindGlobalEvents() {
  document.addEventListener('mouseup', (e) => {
    if (e.target.closest(`#${PANEL_ID}`) || e.target.closest(`#${BTN_ID}`)) return
    setTimeout(() => {
      const sel  = window.getSelection()
      const text = sel?.toString().trim()
      if (!text || text.length < 3) { hideFloatingBtn(); return }
      selectedText = text
      const range = sel.getRangeAt(0)
      showFloatingBtn(range.getBoundingClientRect())
    }, 30)
  })

  document.addEventListener('mousedown', (e) => {
    if (
      e.target.closest(`#${PANEL_ID}`) ||
      e.target.closest(`#${BTN_ID}`) ||
      e.target.closest(`#${OVERLAY_ID}`)
    ) return
    hideFloatingBtn()
  })

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { hidePanel(); hideFloatingBtn() }
  })
}

// ── Start ──────────────────────────────────────────────────────────────────────

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init)
} else {
  init()
}
