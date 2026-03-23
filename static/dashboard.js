const { useState, useEffect, useRef, useCallback } = React;
const { createRoot } = ReactDOM;

const API_BASE = 'http://localhost:8765/api';
const WS_URL = 'ws://localhost:8765/ws';

async function apiFetch(path, method = 'GET', body = null) {
    const options = { method };
    if (body) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({ message: 'Unknown error' }));
        throw new Error(errorData.message || `API error: ${response.status} ${response.statusText}`);
    }
    return response.json();
}

function formatTimestamp(ts) { return ts ? new Date(ts).toLocaleString() : '—'; }

function formatDuration(created, completed) {
    if (!created) return '—';
    const s = Math.floor(((completed ? new Date(completed) : new Date()) - new Date(created)) / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

const getStatusClass = (s) => s ? s.toLowerCase().replace(/ /g, '_') : '';
const statusColor = (s) => ({ pending: 'var(--pending)', in_progress: 'var(--in-progress)', done: 'var(--done)', failed: 'var(--failed)' })[s] || 'var(--muted)';

let toastIdCounter = 0;
const Toast = ({ toasts, onRemoveToast }) => (
    <div className="toast-container">
        {toasts.map(t => (
            <div key={t.id} className={`toast ${t.type}`}>
                <div className="toast-message">{t.message}</div>
                <button className="toast-close-button" onClick={() => onRemoveToast(t.id)}>&times;</button>
            </div>
        ))}
    </div>
);

const TopBar = ({ tasks, daemonStatus, onRestartDaemon }) => {
    const c = tasks.reduce((a, t) => { a[t.status] = (a[t.status] || 0) + 1; return a; }, {});
    return (
        <div className="top-bar">
            <div className="top-bar-left">
                <span className="brand-name">WESA</span>
                <div className="brand-sep"></div>
                <span className="brand-sub">Agent Ecosystem</span>
            </div>
            <div className="top-bar-center">
                <div className="status-pill pending">PENDING: {c.pending || 0}</div>
                <div className="status-pill in-progress">ACTIVE: {c.in_progress || 0}</div>
                <div className="status-pill done">DONE: {c.done || 0}</div>
                <div className="status-pill failed">FAILED: {c.failed || 0}</div>
            </div>
            <div className="top-bar-right">
                <div className={`daemon-status-dot ${daemonStatus.running ? 'green' : 'red'}`}></div>
                <span className="daemon-pid">PID: {daemonStatus.pid || '—'}</span>
                <button onClick={onRestartDaemon}>Restart</button>
            </div>
        </div>
    );
};

const DaemonHealthPanel = ({ daemonStatus, tasks }) => {
    const running = daemonStatus.running;
    const c = tasks.reduce((a, t) => { a[t.status] = (a[t.status] || 0) + 1; return a; }, {});
    return (
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', background: running ? 'var(--surface)' : 'rgba(255,51,51,0.05)', flexShrink: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <span style={{ fontFamily: 'var(--font-ui)', fontSize: '0.68em', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--muted)' }}>Daemon Health</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: running ? 'var(--done)' : 'var(--failed)' }}>● {running ? 'RUNNING' : 'STOPPED'}</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '10px' }}>
                {[
                    { label: 'PID',     value: daemonStatus.pid || '—', color: null },
                    { label: 'PENDING', value: c.pending || 0,          color: 'var(--pending)' },
                    { label: 'ACTIVE',  value: c.in_progress || 0,      color: 'var(--in-progress)' },
                    { label: 'DONE',    value: c.done || 0,             color: 'var(--done)' },
                    { label: 'FAILED',  value: c.failed || 0,           color: c.failed ? 'var(--failed)' : null },
                ].map(({ label, value, color }) => (
                    <div key={label}>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.62em', color: 'var(--muted)', letterSpacing: '0.08em', marginBottom: '3px' }}>{label}</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '1.05em', fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
                    </div>
                ))}
            </div>
        </div>
    );
};

const SORT_FIELDS = { STATUS: 'status', MODEL: 'model', CREATED: 'created_at' };

const TaskQueueTable = ({ tasks, selectedTaskId, onSelectTask }) => {
    const [filterToday, setFilterToday] = useState(true);
    const [sortField, setSortField] = useState('created_at');
    const [sortDir, setSortDir] = useState('desc');
    const today = new Date().toDateString();

    const handleSort = (field) => {
        if (sortField === field) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDir('desc');
        }
    };

    const sorted = [...tasks].sort((a, b) => {
        let va = a[sortField] || '';
        let vb = b[sortField] || '';
        if (sortField === 'created_at') { return sortDir === 'asc' ? new Date(va) - new Date(vb) : new Date(vb) - new Date(va); }
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    });

    const filtered = filterToday
        ? sorted.filter(t => new Date(t.created_at).toDateString() === today)
        : sorted;

    const thStyle = (field) => ({
        padding: '7px 12px', textAlign: 'left', fontWeight: 700, fontSize: '0.72em',
        letterSpacing: '0.1em', borderBottom: '2px solid var(--border)', whiteSpace: 'nowrap',
        color: sortField === field ? 'var(--text)' : 'var(--muted)',
        cursor: field ? 'pointer' : 'default', userSelect: 'none',
    });
    const sortIcon = (field) => sortField === field ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

    return (
        <div style={{ display: 'flex', flexDirection: 'column', flexGrow: 1, overflow: 'hidden', minHeight: 0 }}>
            <div style={{ padding: '7px 14px', borderBottom: '1px solid var(--border)', display: 'flex', gap: '8px', alignItems: 'center', background: 'var(--surface2)', flexShrink: 0 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72em', color: 'var(--muted)', letterSpacing: '0.06em' }}>{filtered.length} TASK{filtered.length !== 1 ? 'S' : ''}</span>
                <button onClick={() => setFilterToday(f => !f)} style={{ fontSize: '0.68em', padding: '2px 8px', background: filterToday ? 'var(--done)' : 'var(--surface)', color: filterToday ? '#000' : 'var(--muted)', border: filterToday ? 'none' : '1px solid var(--border)', borderRadius: '1px', cursor: 'pointer', fontFamily: 'var(--font-mono)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                    {filterToday ? 'Today' : 'All time'}
                </button>
            </div>
            <div style={{ overflowY: 'auto', flexGrow: 1 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: '0.78em' }}>
                    <thead>
                        <tr style={{ background: 'var(--surface2)', position: 'sticky', top: 0, zIndex: 1 }}>
                            <th style={thStyle(null)}>ID</th>
                            <th style={thStyle(null)}>TYPE</th>
                            <th style={thStyle('model')} onClick={() => handleSort('model')}>MODEL{sortIcon('model')}</th>
                            <th style={thStyle('status')} onClick={() => handleSort('status')}>STATUS{sortIcon('status')}</th>
                            <th style={thStyle('created_at')} onClick={() => handleSort('created_at')}>CREATED{sortIcon('created_at')}</th>
                            <th style={thStyle(null)}>DURATION</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filtered.length === 0 ? (
                            <tr><td colSpan={6} style={{ padding: '24px 12px', color: 'var(--muted)', textAlign: 'center', letterSpacing: '0.06em' }}>— no tasks —</td></tr>
                        ) : filtered.map(t => (
                            <tr key={t.id} onClick={() => onSelectTask(t.id)}
                                style={{ borderBottom: '1px solid var(--border)', borderLeft: t.id === selectedTaskId ? '3px solid var(--done)' : '3px solid transparent', background: t.id === selectedTaskId ? 'var(--selected-bg)' : 'transparent', cursor: 'pointer', transition: 'background 0.1s' }}
                                onMouseEnter={e => { if (t.id !== selectedTaskId) e.currentTarget.style.background = 'var(--hover)'; }}
                                onMouseLeave={e => { if (t.id !== selectedTaskId) e.currentTarget.style.background = 'transparent'; }}
                            >
                                <td style={{ padding: '8px 12px', color: 'var(--text)', fontWeight: 700 }}>#{t.id.substring(0,8)}</td>
                                <td style={{ padding: '8px 12px' }}><span style={{ background: 'var(--surface2)', border: '1px solid var(--border)', padding: '1px 5px', borderRadius: '1px', fontSize: '0.9em', letterSpacing: '0.04em', color: 'var(--muted)' }}>{t.type}</span></td>
                                <td style={{ padding: '8px 12px', color: 'var(--muted)' }}>{t.model || '—'}</td>
                                <td style={{ padding: '8px 12px' }}><span style={{ color: statusColor(t.status), fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', fontSize: '0.85em' }}>● {t.status.replace(/_/g,' ')}</span></td>
                                <td style={{ padding: '8px 12px', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{formatTimestamp(t.created_at)}</td>
                                <td style={{ padding: '8px 12px', color: 'var(--muted)' }}>{formatDuration(t.created_at, t.completed_at)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};

const TaskLogPanel = ({ detail, onDeleteTask }) => {
    const [tab, setTab] = useState('overview');
    const tabBtn = (name, label) => (
        <button onClick={() => setTab(name)} style={{ background: 'none', border: 'none', borderBottom: tab === name ? '2px solid var(--done)' : '2px solid transparent', color: tab === name ? 'var(--done)' : 'var(--muted)', padding: '9px 16px', fontSize: '0.72em', fontWeight: 700, letterSpacing: '0.1em', cursor: 'pointer', textTransform: 'uppercase', fontFamily: 'var(--font-ui)', marginBottom: '-1px', borderRadius: 0, transition: 'color 0.15s, border-color 0.15s' }}>{label}</button>
    );
    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', borderLeft: '1px solid var(--border)' }}>
            <div style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)', flexShrink: 0, display: 'flex', alignItems: 'flex-end', padding: '0 4px' }}>
                {tabBtn('overview','Overview')}{tabBtn('result','Result')}{tabBtn('error','Error')}
            </div>
            {!detail ? (
                <div className="no-task-selected">— no task selected —</div>
            ) : (
                <div style={{ padding: '16px 20px', overflowY: 'auto', flexGrow: 1 }}>
                    {tab === 'overview' && <>
                        <h2 style={{ marginTop: 0, marginBottom: '10px', display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: '8px', borderBottom: '1px solid var(--border)', paddingBottom: '10px' }}>
                            <span className="task-id-header">#{detail.id.substring(0,8)}</span>
                            <span className="task-type-tag">{detail.type}</span>
                            <span className={`task-status-header ${getStatusClass(detail.status)}`}>{detail.status.replace(/_/g,' ')}</span>
                        </h2>
                        <div className="task-meta-line">
                            <span>MODEL: {detail.model || '—'}</span>
                            <span>CREATED: {formatTimestamp(detail.created_at)}</span>
                            <span>COMPLETED: {formatTimestamp(detail.completed_at)}</span>
                        </div>
                        <div className="task-detail-section"><h3>Prompt</h3><pre className="task-detail-pre">{detail.prompt}</pre></div>
                    </>}
                    {tab === 'result' && <div className="task-detail-section">
                        <h3>Result</h3>
                        {detail.result ? <pre className="task-detail-pre">{detail.result}</pre> : <div style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: '0.82em' }}>No result yet.</div>}
                        {detail.report && <><h3 style={{ marginTop: '20px' }}>Report</h3><pre className="task-detail-pre">{detail.report}</pre></>}
                    </div>}
                    {tab === 'error' && <div className="task-detail-section">
                        <h3>Error</h3>
                        {detail.error ? <pre className="task-detail-pre error">{detail.error}</pre> : <div style={{ color: 'var(--muted)', fontFamily: 'var(--font-mono)', fontSize: '0.82em' }}>No errors.</div>}
                    </div>}
                    <div className="delete-button-container">
                        <button className="btn-delete" onClick={() => onDeleteTask(detail.id)}>Delete Task</button>
                    </div>
                </div>
            )}
        </div>
    );
};

const DesignsPanel = ({ addToast }) => {
    const [designs, setDesigns] = React.useState([]);
    const [selectedDesign, setSelectedDesign] = React.useState(null);
    const [loading, setLoading] = React.useState(true);
    const [actionInProgress, setActionInProgress] = React.useState(null);
    const [retryFeedback, setRetryFeedback] = React.useState('');
    const [showRetryInput, setShowRetryInput] = React.useState(null);
    const hasInitialSelection = React.useRef(false);

    const fetchDesigns = React.useCallback(async () => {
        try {
            const data = await apiFetch('/designs');
            setDesigns(data);
            if (data.length > 0 && !hasInitialSelection.current) { hasInitialSelection.current = true; setSelectedDesign(data[0]); }
        } catch (err) { addToast('Failed to load designs: ' + err.message, 'error'); }
        finally { setLoading(false); }
    }, [addToast]);

    React.useEffect(() => { fetchDesigns(); const i = setInterval(fetchDesigns, 5000); return () => clearInterval(i); }, [fetchDesigns]);

    const handleApprove = React.useCallback(async (design) => {
        if (!window.confirm(`Approve "${design.name}"?\n\nThis will queue UX1 polish + FE1 plan + Q1 review.\nOther concepts remain available for approval.`)) return;
        setActionInProgress(design.filename);
        try {
            const result = await apiFetch('/designs/approve', 'POST', { filename: design.filename });
            addToast(`Approved "${design.name}". Tasks queued: polish ${result.polish_task}, FE1 ${result.fe1_task}, Q1 ${result.q1_review_task}.`, 'success');
            hasInitialSelection.current = false; await fetchDesigns();
        } catch (err) { addToast('Approve failed: ' + err.message, 'error'); }
        finally { setActionInProgress(null); }
    }, [addToast, fetchDesigns]);

    const handleRetry = React.useCallback(async (design) => {
        setActionInProgress(design.filename); setShowRetryInput(null);
        try {
            const result = await apiFetch('/designs/retry', 'POST', { filename: design.filename, feedback: retryFeedback });
            addToast(`Retry queued for "${design.name}" → task ${result.retry_task}.`, 'success');
            setRetryFeedback(''); setDesigns(prev => prev.filter(d => d.filename !== design.filename));
            if (selectedDesign && selectedDesign.filename === design.filename) setSelectedDesign(null);
        } catch (err) { addToast('Retry failed: ' + err.message, 'error'); }
        finally { setActionInProgress(null); }
    }, [addToast, retryFeedback, selectedDesign]);

    const handleDelete = React.useCallback(async (design) => {
        if (!window.confirm(`Dismiss "${design.name}"? File will be deleted.`)) return;
        try {
            await apiFetch('/designs/delete', 'POST', { filename: design.filename });
            setDesigns(prev => prev.filter(d => d.filename !== design.filename));
            if (selectedDesign && selectedDesign.filename === design.filename) setSelectedDesign(null);
            addToast(`Dismissed "${design.name}".`, 'info');
        } catch (err) { addToast('Delete failed: ' + err.message, 'error'); }
    }, [addToast, selectedDesign]);

    if (loading) return <div className="designs-empty"><span>Loading designs...</span></div>;
    const isConcept  = f => (f.filename.startsWith('concept-') || f.filename.endsWith('_prototype.html')) && !f.filename.startsWith('approved-') && !f.filename.startsWith('polished-');
    const isApproved = f => f.filename.startsWith('approved-');
    const isPolished = f => f.filename.startsWith('polished-');

    return (
        <div className="designs-panel">
            <div className="designs-list">
                <div className="designs-list-header">Designs ({designs.length})</div>
                {designs.length === 0 && <div style={{ padding: '14px', fontSize: '0.78em', color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: '0.04em' }}>No designs yet.</div>}
                {designs.map(d => (
                    <div key={d.filename}>
                        <div className={`designs-list-item ${selectedDesign && selectedDesign.filename === d.filename ? 'selected' : ''}`} onClick={() => setSelectedDesign(d)}>
                            {isApproved(d) && <span style={{ color: 'var(--done)', marginRight: '5px' }}>✓</span>}
                            {isPolished(d) && <span style={{ color: 'var(--in-progress)', marginRight: '5px' }}>★</span>}
                            {d.name}
                        </div>
                        {isConcept(d) && (
                            <div style={{ padding: '0 10px 8px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                <div style={{ display: 'flex', gap: '4px' }}>
                                    <button onClick={() => handleApprove(d)} disabled={actionInProgress === d.filename} style={{ flex:1, fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--done)', border:'1px solid var(--done)', borderRadius:'1px', cursor:'pointer', opacity: actionInProgress===d.filename?0.4:1, fontFamily:'var(--font-mono)', letterSpacing:'0.06em', textTransform:'uppercase' }}>{actionInProgress===d.filename?'...':'Approve'}</button>
                                    <button onClick={() => setShowRetryInput(showRetryInput===d.filename?null:d.filename)} disabled={actionInProgress===d.filename} style={{ flex:1, fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--failed)', border:'1px solid var(--failed)', borderRadius:'1px', cursor:'pointer', opacity: actionInProgress===d.filename?0.4:1, fontFamily:'var(--font-mono)', letterSpacing:'0.06em', textTransform:'uppercase' }}>Retry</button>
                                    <button onClick={() => handleDelete(d)} disabled={actionInProgress===d.filename} style={{ fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--muted)', border:'1px solid var(--border)', borderRadius:'1px', cursor:'pointer', fontFamily:'var(--font-mono)' }}>✕</button>
                                </div>
                                {showRetryInput === d.filename && (
                                    <div style={{ display:'flex', flexDirection:'column', gap:'4px' }}>
                                        <textarea placeholder="Feedback for retry (optional)..." value={retryFeedback} onChange={e => setRetryFeedback(e.target.value)} rows={2} style={{ fontSize:'0.72em', padding:'5px 6px', background:'var(--bg)', color:'var(--text)', border:'1px solid var(--border)', borderRadius:'1px', resize:'none', fontFamily:'var(--font-mono)' }} />
                                        <button onClick={() => handleRetry(d)} style={{ fontSize:'0.68em', padding:'4px 6px', background:'transparent', color:'var(--in-progress)', border:'1px solid var(--in-progress)', borderRadius:'1px', cursor:'pointer', fontFamily:'var(--font-mono)', letterSpacing:'0.06em', textTransform:'uppercase' }}>Send Retry</button>
                                    </div>
                                )}
                            </div>
                        )}
                        {isApproved(d) && <div style={{ padding:'0 10px 8px', fontSize:'0.7em', color:'var(--done)', fontFamily:'var(--font-mono)', letterSpacing:'0.04em' }}>Approved — polish + FE1 + Q1 queued</div>}
                        {isPolished(d) && <div style={{ padding:'0 10px 8px', fontSize:'0.7em', color:'var(--muted)', fontFamily:'var(--font-mono)', letterSpacing:'0.04em' }}>Polished</div>}
                    </div>
                ))}
                <div style={{ padding: '10px', borderTop: '1px solid var(--border)' }}>
                    <button onClick={fetchDesigns} style={{ width: '100%', fontSize: '0.72em' }}>Refresh</button>
                </div>
            </div>
            <div className="designs-preview">
                {selectedDesign ? (<>
                    <div className="designs-preview-toolbar">
                        <span style={{ color:'var(--text)', fontWeight:600 }}>{selectedDesign.name}</span>
                        {isApproved(selectedDesign) && <span style={{ color:'var(--done)' }}>✓ Approved</span>}
                        {isPolished(selectedDesign) && <span style={{ color:'var(--in-progress)' }}>★ Polished</span>}
                        <a href={selectedDesign.url} target="_blank" rel="noopener noreferrer" style={{ marginLeft:'auto', color:'var(--in-progress)', textDecoration:'none', fontSize:'0.8em', fontFamily:'var(--font-mono)', letterSpacing:'0.04em' }}>Open in new tab →</a>
                    </div>
                    <iframe key={selectedDesign.filename} src={selectedDesign.url} title={selectedDesign.name} />
                </>) : <div className="designs-empty"><span>— select a concept to preview —</span></div>}
            </div>
        </div>
    );
};

const ReviewsPanel = () => {
    const [reviews, setReviews] = React.useState([]);
    const [selected, setSelected] = React.useState(null);
    const [content, setContent] = React.useState('');
    const [loading, setLoading] = React.useState(true);

    React.useEffect(() => { apiFetch('/reviews').then(d => { setReviews(d); setLoading(false); }).catch(() => setLoading(false)); }, []);

    const selectReview = async (r) => {
        setSelected(r); setContent('Loading...');
        try { const d = await apiFetch(`/reviews/${r.filename}`); setContent(d.content); }
        catch (e) { setContent('Failed to load review.'); }
    };

    if (loading) return <div style={{ padding:'24px', color:'var(--muted)', fontFamily:'var(--font-mono)', fontSize:'0.82em', letterSpacing:'0.06em' }}>Loading reviews...</div>;

    return (
        <div style={{ display:'flex', height:'100%', overflow:'hidden' }}>
            <div style={{ width:'240px', borderRight:'1px solid var(--border)', overflowY:'auto', flexShrink:0, background:'var(--surface)' }}>
                <div style={{ padding:'8px 14px', fontSize:'0.68em', fontWeight:700, color:'var(--muted)', borderBottom:'1px solid var(--border)', textTransform:'uppercase', letterSpacing:'0.1em', background:'var(--surface2)' }}>Q1 Reviews ({reviews.length})</div>
                {reviews.length === 0 && <div style={{ padding:'16px 14px', fontSize:'0.78em', color:'var(--muted)', fontFamily:'var(--font-mono)' }}>No reviews yet.</div>}
                {reviews.map(r => (
                    <div key={r.filename} onClick={() => selectReview(r)} style={{ padding:'10px 14px', cursor:'pointer', borderBottom:'1px solid var(--border)', background: selected&&selected.filename===r.filename?'var(--selected-bg)':'transparent', borderLeft: selected&&selected.filename===r.filename?'3px solid var(--in-progress)':'3px solid transparent', fontSize:'0.8em' }}>
                        <div style={{ fontFamily:'var(--font-mono)', fontSize:'0.85em', fontWeight:700, color: r.type==='rejection'?'var(--failed)':'var(--done)', marginBottom:'3px', letterSpacing:'0.06em' }}>{r.type==='rejection'?'✕ Rejected':'✓ Approved'}</div>
                        <div style={{ color:'var(--text)', wordBreak:'break-all', fontWeight:500 }}>{r.concept}</div>
                    </div>
                ))}
            </div>
            <div style={{ flex:1, overflowY:'auto', padding:'18px 24px' }}>
                {content ? <pre style={{ fontFamily:'var(--font-mono)', fontSize:'0.78em', color:'var(--text)', whiteSpace:'pre-wrap', lineHeight:1.7, margin:0 }}>{content}</pre>
                         : <div style={{ color:'var(--muted)', fontFamily:'var(--font-mono)', fontSize:'0.82em', letterSpacing:'0.06em' }}>— select a review to read it —</div>}
            </div>
        </div>
    );
};

const App = () => {
    const [tasks, setTasks] = useState([]);
    const [selectedTaskId, setSelectedTaskId] = useState(null);
    const [selectedTaskDetail, setSelectedTaskDetail] = useState(null);
    const [daemonStatus, setDaemonStatus] = useState({ running: false, pid: null });
    const [toasts, setToasts] = useState([]);
    const [activeTab, setActiveTab] = useState('tasks');
    const wsRef = useRef(null);
    const wsReconnectTimeoutRef = useRef(null);

    const addToast = useCallback((message, type = 'info') => {
        const id = toastIdCounter++;
        setToasts(prev => [...prev, { id, message, type }]);
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
    }, []);

    const removeToast = useCallback((id) => setToasts(prev => prev.filter(t => t.id !== id)), []);

    const fetchInitialData = useCallback(async () => {
        try {
            const [tasksData, daemonData] = await Promise.all([apiFetch('/tasks'), apiFetch('/daemon/status')]);
            setTasks(tasksData); setDaemonStatus(daemonData);
        } catch (error) { addToast(`Failed to load initial data: ${error.message}`, 'error'); }
    }, [addToast]);

    const connectWebSocket = useCallback(() => {
        if (wsReconnectTimeoutRef.current) { clearTimeout(wsReconnectTimeoutRef.current); wsReconnectTimeoutRef.current = null; }
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) wsRef.current.close();
        wsRef.current = new WebSocket(WS_URL);
        wsRef.current.onopen = () => addToast('Connected to real-time updates.', 'success');
        wsRef.current.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (Array.isArray(data)) setTasks(data);
            else if (data.daemonStatus) setDaemonStatus(data.daemonStatus);
        };
        wsRef.current.onclose = () => { addToast('WebSocket disconnected. Reconnecting in 5s...', 'warning'); wsReconnectTimeoutRef.current = setTimeout(connectWebSocket, 5000); };
        wsRef.current.onerror = () => { if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) wsRef.current.close(); };
    }, [addToast]);

    useEffect(() => {
        fetchInitialData(); connectWebSocket();
        return () => { if (wsRef.current) wsRef.current.close(); if (wsReconnectTimeoutRef.current) clearTimeout(wsReconnectTimeoutRef.current); };
    }, [fetchInitialData, connectWebSocket]);

    useEffect(() => {
        if (!selectedTaskId) { setSelectedTaskDetail(null); return; }
        apiFetch(`/tasks/${selectedTaskId}`).then(setSelectedTaskDetail).catch(err => { setSelectedTaskDetail(null); addToast(`Failed to load task detail: ${err.message}`, 'error'); });
    }, [selectedTaskId, addToast]);

    const handleTaskSelect   = useCallback((id) => setSelectedTaskId(id), []);
    const handleTaskDelete   = useCallback(async (id) => {
        if (window.confirm(`Delete task ${id.substring(0,8)}?`)) {
            try {
                await apiFetch(`/tasks/${id}`, 'DELETE');
                setTasks(prev => prev.filter(t => t.id !== id));
                if (selectedTaskId === id) { setSelectedTaskId(null); setSelectedTaskDetail(null); }
                addToast(`Task ${id.substring(0,8)} deleted.`, 'success');
            } catch (error) { addToast(`Failed to delete task: ${error.message}`, 'error'); }
        }
    }, [selectedTaskId, addToast]);

    const handleDaemonRestart = useCallback(async () => {
        try {
            setDaemonStatus(prev => ({ ...prev, running: false, pid: null }));
            const result = await apiFetch('/daemon/restart', 'POST');
            setDaemonStatus({ running: true, pid: result.pid });
            addToast('Daemon restarted.', 'success');
        } catch (error) { addToast(`Failed to restart daemon: ${error.message}`, 'error'); }
    }, [addToast]);

    return (
        <>
            <TopBar tasks={tasks} daemonStatus={daemonStatus} onRestartDaemon={handleDaemonRestart} />
            <div className="tab-nav">
                <button className={activeTab==='tasks'     ?'active':''} onClick={() => setActiveTab('tasks')}>Tasks</button>
                <button className={activeTab==='designs'   ?'active':''} onClick={() => setActiveTab('designs')}>Designs</button>
                <button className={activeTab==='reviews'   ?'active':''} onClick={() => setActiveTab('reviews')}>Q1 Reviews</button>
                <button className={activeTab==='pipelines' ?'active':''} onClick={() => setActiveTab('pipelines')}>Pipelines</button>
            </div>
            {activeTab === 'tasks' && (
                <div style={{ display:'grid', gridTemplateColumns:'8fr 4fr', flexGrow:1, overflow:'hidden', minHeight:0 }}>
                    <div style={{ display:'flex', flexDirection:'column', overflow:'hidden' }}>
                        <DaemonHealthPanel daemonStatus={daemonStatus} tasks={tasks} />
                        <TaskQueueTable tasks={tasks} selectedTaskId={selectedTaskId} onSelectTask={handleTaskSelect} />
                    </div>
                    <TaskLogPanel detail={selectedTaskDetail} onDeleteTask={handleTaskDelete} />
                </div>
            )}
            {activeTab === 'designs'   && <div className="main-content"><DesignsPanel addToast={addToast} /></div>}
            {activeTab === 'reviews'   && <div className="main-content"><ReviewsPanel /></div>}
            {activeTab === 'pipelines' && <div className="main-content"><PipelineLauncherPanel addToast={addToast} /></div>}
            <Toast toasts={toasts} onRemoveToast={removeToast} />
        </>
    );
};

const PIPELINES = ['research-center', 'design-loop'];

const PipelineLauncherPanel = ({ addToast }) => {
    const [pipeline, setPipeline] = useState('research-center');
    const [topic, setTopic]       = useState('');
    const [loading, setLoading]   = useState(false);

    const handleLaunch = async () => {
        if (!topic.trim()) { addToast('Topic is required.', 'error'); return; }
        setLoading(true);
        try {
            const data = await apiFetch('/pipeline/start', 'POST', { pipeline, topic: topic.trim() });
            addToast(`Pipeline queued — task: ${data.task_id}`, 'success');
            setTopic('');
        } catch (err) {
            addToast(`Failed: ${err.message}`, 'error');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ maxWidth: 480, margin: '2rem auto' }}>
            <div className="panel-header" style={{ marginBottom: '1.5rem' }}>Pipeline Launcher</div>
            <div className="task-detail-section" style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
                <label style={{ fontSize:'0.75rem', color:'var(--muted)', textTransform:'uppercase', letterSpacing:'0.05em' }}>
                    Pipeline
                    <select
                        value={pipeline}
                        onChange={e => setPipeline(e.target.value)}
                        style={{ display:'block', marginTop:'0.4rem', width:'100%', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--fg)', padding:'0.5rem 0.75rem', fontSize:'0.875rem' }}
                    >
                        {PIPELINES.map(p => <option key={p} value={p}>{p}</option>)}
                    </select>
                </label>
                <label style={{ fontSize:'0.75rem', color:'var(--muted)', textTransform:'uppercase', letterSpacing:'0.05em' }}>
                    Topic
                    <input
                        type="text"
                        value={topic}
                        onChange={e => setTopic(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleLaunch()}
                        placeholder="e.g. AgentHire portal design patterns 2026"
                        style={{ display:'block', marginTop:'0.4rem', width:'100%', background:'var(--bg)', border:'1px solid var(--border)', borderRadius:4, color:'var(--fg)', padding:'0.5rem 0.75rem', fontSize:'0.875rem', boxSizing:'border-box' }}
                    />
                </label>
                <button
                    onClick={handleLaunch}
                    disabled={loading}
                    className="btn-primary"
                    style={{ marginTop:'0.5rem', opacity: loading ? 0.6 : 1 }}
                >
                    {loading ? 'Queuing...' : 'Start Pipeline'}
                </button>
            </div>
        </div>
    );
};

createRoot(document.getElementById('root')).render(<App />);
