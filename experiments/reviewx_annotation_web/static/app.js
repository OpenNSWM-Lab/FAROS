const scoreFields = [
  ['humanCorrectness', '正确性'],
  ['humanActionability', '可执行性'],
  ['humanSpecificity', '专家问题覆盖'],
  ['humanGrounding', '证据一致性'],
  ['humanSeverityAgreement', '严重性一致'],
]
const coverageLabels = [
  ['covered', '完全覆盖'],
  ['partial', '部分覆盖'],
  ['not_covered', '未覆盖'],
  ['invalid_question', '问题无效'],
  ['insufficient_context', '上下文不足'],
]

const state = {
  annotatorId: '',
  tasks: [],
  progress: { total: 0, completed: 0, draft: 0, remaining: 0 },
  selectedId: null,
  saving: false,
  activeBatchId: '',
  activeBatchName: '',
}

const elements = {
  loginView: document.querySelector('#login-view'),
  appView: document.querySelector('#app-view'),
  loginForm: document.querySelector('#login-form'),
  loginError: document.querySelector('#login-error'),
  annotatorId: document.querySelector('#annotator-id'),
  accessCode: document.querySelector('#access-code'),
  annotatorLabel: document.querySelector('#annotator-label'),
  logoutButton: document.querySelector('#logout-button'),
  progressLabel: document.querySelector('#progress-label'),
  progressFill: document.querySelector('#progress-fill'),
  saveState: document.querySelector('#save-state'),
  taskSearch: document.querySelector('#task-search'),
  taskFilter: document.querySelector('#task-filter'),
  taskList: document.querySelector('#task-list'),
  emptyState: document.querySelector('#empty-state'),
  annotationForm: document.querySelector('#annotation-form'),
  taskPosition: document.querySelector('#task-position'),
  taskTitle: document.querySelector('#task-title'),
  taskBadges: document.querySelector('#task-badges'),
  claimText: document.querySelector('#claim-text'),
  claimSection: document.querySelector('#claim-section'),
  sampleId: document.querySelector('#sample-id'),
  reviewerAssessment: document.querySelector('#reviewer-assessment'),
  findingTitle: document.querySelector('#finding-title'),
  findingDescription: document.querySelector('#finding-description'),
  expertQuestionSection: document.querySelector('#expert-question-section'),
  expertQuestion: document.querySelector('#expert-question'),
  authorAnswerSection: document.querySelector('#author-answer-section'),
  authorAnswer: document.querySelector('#author-answer'),
  answerabilityBadge: document.querySelector('#answerability-badge'),
  referenceEvidenceSection: document.querySelector('#reference-evidence-section'),
  referenceEvidence: document.querySelector('#reference-evidence'),
  suggestedFix: document.querySelector('#suggested-fix'),
  ratingFields: document.querySelector('#rating-fields'),
  coverageFields: document.querySelector('#coverage-fields'),
  humanNotes: document.querySelector('#human-notes'),
  annotationError: document.querySelector('#annotation-error'),
  saveButton: document.querySelector('#save-button'),
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  })
  if (response.status === 401) {
    showLogin()
    throw new Error('请重新登录')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`)
  return data
}

function showLogin(clearAccessCode = false) {
  elements.appView.hidden = true
  elements.loginView.hidden = false
  if (clearAccessCode) elements.accessCode.value = ''
}

function showApp() {
  elements.loginView.hidden = true
  elements.appView.hidden = false
  elements.annotatorLabel.textContent = state.annotatorId
}

function buildRatingFields() {
  elements.ratingFields.innerHTML = scoreFields.map(([field, label]) => `
    <div class="rating-row" data-field="${field}">
      <div class="rating-label">${label}</div>
      ${[1, 2, 3, 4, 5].map(value => `
        <label class="rating-option">
          <input type="radio" name="${field}" value="${value}" />
          <span>${value}</span>
        </label>
      `).join('')}
    </div>
  `).join('')
}

function buildCoverageFields() {
  elements.coverageFields.innerHTML = coverageLabels.map(([value, label]) => `
    <label class="coverage-option">
      <input type="radio" name="humanCoverageLabel" value="${value}" />
      <span>${label}</span>
    </label>
  `).join('')
}

function filteredTasks() {
  const query = elements.taskSearch.value.trim().toLowerCase()
  const filter = elements.taskFilter.value
  return state.tasks.filter(task => {
    const status = task.annotation?.status || 'unrated'
    if (filter !== 'all' && status !== filter) return false
    if (!query) return true
    const content = [task.claimText, task.riskType, task.sampleId, task.reviewerAssessment,
      task.expertReviewerQuestion, task.authorAnswer, task.referenceEvidence]
      .filter(Boolean).join(' ').toLowerCase()
    return content.includes(query)
  })
}

function renderTaskList() {
  const tasks = filteredTasks()
  elements.taskList.innerHTML = ''
  tasks.forEach(task => {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = `task-item${task.annotationId === state.selectedId ? ' active' : ''}`
    const status = task.annotation?.status || 'unrated'
    button.innerHTML = `
      <span class="task-number">${String(task.position).padStart(2, '0')}</span>
      <span class="task-summary">
        <strong>${escapeHtml(task.riskType || task.supportStatus || 'Review finding')}</strong>
        <span>${escapeHtml(task.expertReviewerQuestion || task.claimText || '')}</span>
      </span>
      <span class="status-dot ${status}" aria-label="${status}"></span>
    `
    button.addEventListener('click', () => selectTask(task.annotationId))
    elements.taskList.appendChild(button)
  })
}

function renderProgress() {
  const { total, completed } = state.progress
  const percent = total ? Math.round(completed / total * 100) : 0
  elements.progressLabel.textContent = `${completed} / ${total} 已完成`
  elements.progressFill.style.width = `${percent}%`
}

function selectTask(taskId) {
  state.selectedId = taskId
  const task = state.tasks.find(item => item.annotationId === taskId)
  if (!task) return
  renderTaskList()
  elements.emptyState.hidden = true
  elements.annotationForm.hidden = false
  elements.taskPosition.textContent = `任务 ${task.position} / ${state.tasks.length}`
  elements.taskTitle.textContent = task.riskType || 'Review finding'
  elements.claimText.textContent = task.claimText || '—'
  elements.claimSection.textContent = task.claimSection || 'Unknown section'
  elements.sampleId.textContent = task.sampleId || ''
  elements.reviewerAssessment.textContent = task.reviewerAssessment || '—'
  elements.findingTitle.textContent = task.findingTitle || ''
  elements.findingDescription.textContent = task.findingDescription || ''
  elements.findingTitle.hidden = !task.findingTitle
  elements.findingDescription.hidden = !task.findingDescription
    || task.findingDescription.trim() === String(task.reviewerAssessment || '').trim()
  elements.expertQuestionSection.hidden = !task.expertReviewerQuestion
  elements.expertQuestion.textContent = task.expertReviewerQuestion || ''
  const hasAuthorAnswer = task.authorAnswer || task.authorAnswerable !== undefined
  elements.authorAnswerSection.hidden = !hasAuthorAnswer
  elements.authorAnswer.textContent = task.authorAnswer || '作者未提供自由文本回答。'
  const answerable = String(task.authorAnswerable).toLowerCase()
  elements.answerabilityBadge.textContent = answerable === 'true' ? '可回答' : answerable === 'false' ? '不可回答' : '未标注'
  elements.referenceEvidenceSection.hidden = !task.referenceEvidence
  elements.referenceEvidence.textContent = task.referenceEvidence || ''
  elements.suggestedFix.textContent = task.suggestedFix || '—'
  const badges = [task.severity, task.supportStatus, task.method].filter(Boolean)
  elements.taskBadges.innerHTML = badges.map((value, index) => {
    const severityClass = index === 0 ? ` severity-${String(value).toLowerCase()}` : ''
    return `<span class="badge${severityClass}">${escapeHtml(value)}</span>`
  }).join('')
  const annotation = task.annotation || {}
  scoreFields.forEach(([field]) => {
    document.querySelectorAll(`input[name="${field}"]`).forEach(input => {
      input.checked = Number(input.value) === Number(annotation[field])
    })
  })
  document.querySelectorAll('input[name="humanCoverageLabel"]').forEach(input => {
    input.checked = input.value === annotation.humanCoverageLabel
  })
  elements.humanNotes.value = annotation.humanNotes || ''
  elements.annotationError.textContent = ''
  elements.saveState.textContent = annotation.updatedAt ? `已保存 ${formatTime(annotation.updatedAt)}` : ''
}

function annotationPayload() {
  const task = state.tasks.find(item => item.annotationId === state.selectedId)
  const payload = {
    annotationId: state.selectedId,
    batchId: task?.batchId || state.activeBatchId,
    humanNotes: elements.humanNotes.value,
  }
  scoreFields.forEach(([field]) => {
    const selected = document.querySelector(`input[name="${field}"]:checked`)
    payload[field] = selected ? Number(selected.value) : null
  })
  payload.humanCoverageLabel = document.querySelector('input[name="humanCoverageLabel"]:checked')?.value || null
  return payload
}

async function saveAnnotation(moveNext) {
  if (!state.selectedId || state.saving) return
  state.saving = true
  elements.annotationError.textContent = ''
  elements.saveState.textContent = '保存中…'
  try {
    const data = await api('/api/annotations', { method: 'POST', body: JSON.stringify(annotationPayload()) })
    const task = state.tasks.find(item => item.annotationId === state.selectedId)
    task.annotation = data.annotation
    state.progress = data.progress
    renderTaskList()
    renderProgress()
    elements.saveState.textContent = `已保存 ${formatTime(data.annotation.updatedAt)}`
    if (moveNext) selectNextTask()
  } catch (error) {
    elements.annotationError.textContent = error.message
    elements.saveState.textContent = ''
  } finally {
    state.saving = false
  }
}

function selectNextTask() {
  const currentIndex = state.tasks.findIndex(item => item.annotationId === state.selectedId)
  const nextUnfinished = state.tasks.find((item, index) => index > currentIndex && item.annotation?.status !== 'completed')
    || state.tasks.find(item => item.annotation?.status !== 'completed')
  if (nextUnfinished) selectTask(nextUnfinished.annotationId)
}

async function loadTasks() {
  const data = await api('/api/tasks')
  state.tasks = data.tasks
  state.activeBatchId = data.batch?.id || state.tasks[0]?.batchId || ''
  state.activeBatchName = data.batch?.name || state.activeBatchId
  elements.annotatorLabel.textContent = `${state.annotatorId} · ${state.activeBatchName}`
  state.progress = data.progress
  renderProgress()
  renderTaskList()
  const first = state.tasks.find(task => task.annotation?.status !== 'completed') || state.tasks[0]
  if (first) selectTask(first.annotationId)
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char])
}

function formatTime(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

elements.loginForm.addEventListener('submit', async event => {
  event.preventDefault()
  elements.loginError.textContent = ''
  try {
    const data = await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({ annotatorId: elements.annotatorId.value.trim(), accessCode: elements.accessCode.value }),
    })
    state.annotatorId = data.annotatorId
    showApp()
    await loadTasks()
  } catch (error) {
    elements.loginError.textContent = error.message
  }
})

elements.annotationForm.addEventListener('submit', event => {
  event.preventDefault()
  saveAnnotation(true)
})
elements.saveButton.addEventListener('click', () => saveAnnotation(false))
elements.taskSearch.addEventListener('input', renderTaskList)
elements.taskFilter.addEventListener('change', renderTaskList)
elements.logoutButton.addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST', body: '{}' }) } catch (_) {}
  state.tasks = []
  state.selectedId = null
  showLogin(true)
})

buildRatingFields()
buildCoverageFields()
api('/api/me').then(data => {
  state.annotatorId = data.annotatorId
  state.progress = data.progress
  showApp()
  loadTasks()
}).catch(() => showLogin())
