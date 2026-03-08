import React, { useState, useEffect } from 'react';
import { getContext, getCoveragePriorities, getDebugPriorities, generateVerificationPlan, getExplanation } from '../services/api';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ScatterChart, Scatter, ZAxis } from 'recharts';

const COLORS = {
  high: '#FB5E4C',
  medium: '#F7CC66',
  low: '#98E4C1',
};

function Dashboard({ onNavigate, runId, setRunId, featureRunId, setFeatureRunId, refreshKey }) {
  const [contextData, setContextData] = useState([]);
  const [coverageData, setCoverageData] = useState([]);
  const [debugData, setDebugData] = useState([]);
  const [planData, setPlanData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedModule, setSelectedModule] = useState(null);
  const [selectedExplanation, setSelectedExplanation] = useState(null);
  const [explanationData, setExplanationData] = useState(null);
  const [stats, setStats] = useState({
    totalModules: 0,
    totalTests: 0,
    totalFailures: 0,
    highRisk: 0,
    mediumRisk: 0,
    lowRisk: 0,
  });
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchData();
  }, [runId, refreshKey]);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      // Fetch context
      const contextResult = await getContext(runId);
      const items = contextResult.items || [];
      setContextData(items);

      const high = items.filter(i => i.risk_level === 'high').length;
      const medium = items.filter(i => i.risk_level === 'medium').length;
      const low = items.filter(i => i.risk_level === 'low').length;

      setStats({
        totalModules: items.length,
        totalTests: items.reduce((sum, m) => sum + (m.tests_total || 0), 0),
        totalFailures: items.reduce((sum, m) => sum + (m.failures_total || 0), 0),
        highRisk: high,
        mediumRisk: medium,
        lowRisk: low,
      });

      if (items.length > 0) {
        setSelectedModule(items[0]);
      }

      // Fetch coverage priorities
      try {
        const coverageResult = await getCoveragePriorities(runId, featureRunId, 100);
        setCoverageData(coverageResult.items || []);
      } catch (e) {
        console.error('Coverage fetch failed:', e);
        setCoverageData([]);
      }

      // Fetch debug priorities
      try {
        const debugResult = await getDebugPriorities(runId, featureRunId, 100);
        setDebugData(debugResult.items || []);
      } catch (e) {
        console.error('Debug fetch failed:', e);
        setDebugData([]);
      }

      // Fetch verification plan
      try {
        const planResult = await generateVerificationPlan(runId, 10);
        setPlanData(planResult.plan_items || []);
      } catch (e) {
        console.error('Plan fetch failed:', e);
        setPlanData([]);
      }

    } catch (error) {
      console.error('Failed to fetch context:', error);
      setError('Backend is down or data unavailable.');
    }
    setLoading(false);
  };



  const fetchExplanation = async (itemId) => {
    setSelectedExplanation(itemId);
    try {
      const result = await getExplanation(itemId, runId);
      setExplanationData(result);
    } catch (e) {
      console.error('Explanation fetch failed:', e);
      setExplanationData({ item_id: itemId, error: 'Failed to fetch explanation' });
    }
  };

  // Compute score decomposition for selected module
  const getScoreDecomposition = (module) => {
    if (!module || !module.evidence) return [];
    const e = module.evidence;
    const failureRate = e.failure_rate || 0;
    const coverageGap = e.coverage_gap || 50;
    const openIssues = e.open_issues || 0;
    const recentCommits = e.recent_commits || 0;
    const artifactFiles = e.artifact_files || 0;

    return [
      { name: 'Failure Rate (45%)', value: Math.round(failureRate * 0.45 * 100) / 100 },
      { name: 'Coverage Gap (25%)', value: Math.round(coverageGap * 0.25 * 100) / 100 },
      { name: 'Open Issues', value: Math.min(openIssues * 8, 20) },
      { name: 'Recent Commits', value: Math.min(recentCommits * 4, 20) },
      { name: 'Artifact Files', value: Math.min(artifactFiles * 1, 10) },
    ];
  };

  // Repo distribution for coverage/debug
  const getRepoDistribution = (items) => {
    const repoCounts = {};
    items.forEach(item => {
      const repo = item.item_id.split(':')[0];
      repoCounts[repo] = (repoCounts[repo] || 0) + 1;
    });
    return Object.entries(repoCounts).map(([repo, count]) => ({ repo, count }));
  };

  // Heatmap data
  const heatmapData = contextData.slice(0, 30).map(item => ({
    repo: item.repo || 'unknown',
    module: (item.module_name || '').slice(0, 15),
    score: item.context_score || 0,
  }));

  const pieData = [
    { name: 'High Risk', value: stats.highRisk, color: COLORS.high },
    { name: 'Medium Risk', value: stats.mediumRisk, color: COLORS.medium },
    { name: 'Low Risk', value: stats.lowRisk, color: COLORS.low },
  ];

  const barData = contextData
    .sort((a, b) => b.context_score - a.context_score)
    .slice(0, 8)
    .map(item => ({
      name: (item.module_name || '').slice(0, 12),
      score: item.context_score,
      risk: item.risk_level,
    }));

  if (loading) {
    return (
      <div className="main-container">
        <div className="loading">Loading VeriMind4 Intelligence...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="main-container">
        <div className="page-header">
          <h1 className="page-title">VeriMind4 Intelligence Console</h1>
        </div>
        <div style={{ padding: '20px', background: '#ffebee', color: '#c62828', borderRadius: '8px', border: '1px solid #ef5350', marginTop: '20px' }}>
          <h3>System Error</h3>
          <p>{error}</p>
          <button className="btn btn-primary" style={{ marginTop: '10px' }} onClick={fetchData}>Retry Data Fetch</button>
        </div>
      </div>
    );
  }

  const coverageRepoDist = getRepoDistribution(coverageData);
  const debugRepoDist = getRepoDistribution(debugData);

  return (
    <div className="main-container">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 className="page-title">Intelligence Dashboard</h1>
          <p style={{ color: '#444', fontSize: '14px', marginTop: '-5px' }}>
            Active Intelligence Context: <span style={{ fontWeight: 'bold' }}>{runId}</span>
          </p>
        </div>
        <div style={{ textAlign: 'right' }}>
          <button className="btn btn-secondary" onClick={fetchData}>Refresh Data</button>
        </div>
      </div>

      {/* Config Inputs */}
      <div style={{ display: 'flex', gap: '20px', marginBottom: '30px' }}>
        <div className="input-group" style={{ flex: 1, marginBottom: 0 }}>
          <label className="input-label">Run ID</label>
          <input
            className="input-field"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            placeholder="run_m4_dataset_gen"
          />
        </div>
        <div className="input-group" style={{ flex: 1, marginBottom: 0 }}>
          <label className="input-label">Feature Run ID</label>
          <input
            className="input-field"
            value={featureRunId}
            onChange={(e) => setFeatureRunId(e.target.value)}
            placeholder="feat_dataset_gen"
          />
        </div>
        <button className="btn btn-primary" style={{ alignSelf: 'flex-end' }} onClick={fetchData}>
          Refresh Data
        </button>
      </div>

      {/* Stats Grid */}
      <div className="stats-bar" style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '15px' }}>
        <div className="stat-card">
          <div className="stat-label">Total Modules</div>
          <div className="stat-value">{stats.totalModules}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Tests</div>
          <div className="stat-value">{stats.totalTests}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Failures</div>
          <div className="stat-value">{stats.totalFailures}</div>
        </div>
        <div className="stat-card" style={{ borderLeft: `5px solid ${COLORS.high}` }}>
          <div className="stat-label">High Risk</div>
          <div className="stat-value">{stats.highRisk}</div>
        </div>
        <div className="stat-card" style={{ borderLeft: `5px solid ${COLORS.medium}` }}>
          <div className="stat-label">Medium Risk</div>
          <div className="stat-value">{stats.mediumRisk}</div>
        </div>
        <div className="stat-card" style={{ borderLeft: `5px solid ${COLORS.low}` }}>
          <div className="stat-label">Low Risk</div>
          <div className="stat-value">{stats.lowRisk}</div>
        </div>
      </div>

      {/* Cards for 4 Problem Statements */}
      <h2 className="section-title">Verification Intelligence Tasks</h2>
      <div className="cards-grid">
        <div className="card" onClick={() => onNavigate('failure')}>
          <div className="card-icon">1</div>
          <div className="card-title">Failure Prediction</div>
          <div className="card-description">
            Predict which modules are most likely to fail using historical verification data
          </div>
        </div>

        <div className="card" onClick={() => onNavigate('plan')}>
          <div className="card-icon">2</div>
          <div className="card-title">Verification Plan</div>
          <div className="card-description">
            Generate automated verification plans based on AI analysis
          </div>
        </div>

        <div className="card" onClick={() => onNavigate('coverage')}>
          <div className="card-icon">3</div>
          <div className="card-title">Coverage Priority</div>
          <div className="card-description">
            AI-enabled functional coverage prioritization for efficient verification
          </div>
        </div>

        <div className="card" onClick={() => onNavigate('debug')}>
          <div className="card-icon">4</div>
          <div className="card-title">Debug Queue</div>
          <div className="card-description">
            AI-based debug prioritization to fix critical issues first
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="two-columns" style={{ marginTop: '30px' }}>
        <div className="chart-container">
          <h3 className="chart-title">Risk Distribution</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                dataKey="value"
                label={({ name, value }) => `${name}: ${value}`}
              >
                {pieData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} stroke="black" strokeWidth={1} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-container">
          <h3 className="chart-title">Top Modules by Context Score</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={barData}>
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis />
              <Tooltip />
              <Bar dataKey="score" fill="#BE8EF8" stroke="black" strokeWidth={1} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Heatmap */}
      <div className="section" style={{ marginTop: '30px' }}>
        <h2 className="section-title">Failure Risk Heatmap (Repo x Module)</h2>
        <div className="chart-container">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
            gap: '4px',
            maxHeight: '400px',
            overflowY: 'auto'
          }}>
            {heatmapData.map((item, idx) => (
              <div
                key={idx}
                style={{
                  padding: '8px',
                  background: item.score >= 70 ? '#FB5E4C' : item.score >= 40 ? '#F7CC66' : '#98E4C1',
                  border: '1px solid black',
                  fontSize: '11px',
                  textAlign: 'center'
                }}
                title={`${item.repo}:${item.module} - Score: ${item.score}`}
              >
                <div style={{ fontWeight: '600' }}>{item.module || 'unknown'}</div>
                <div style={{ fontSize: '10px' }}>{item.repo || 'unknown'}</div>
                <div style={{ fontWeight: '700' }}>{(item.score || 0).toFixed(1)}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Coverage and Debug Tables Side by Side */}
      <div className="two-columns" style={{ marginTop: '30px' }}>
        {/* Coverage Priorities */}
        <div className="section">
          <h2 className="section-title">Coverage Priorities</h2>
          {coverageRepoDist.length > 0 && (
            <div style={{ height: '150px', marginBottom: '15px' }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={coverageRepoDist} layout="vertical">
                  <XAxis type="number" />
                  <YAxis type="category" dataKey="repo" width={80} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#9AD7FD" stroke="black" strokeWidth={1} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="data-table">
            <table>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Item ID</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {coverageData.slice(0, 20).map((item, idx) => (
                  <tr key={idx} onClick={() => fetchExplanation(item.item_id)} style={{ cursor: 'pointer' }}>
                    <td>{item.rank || idx + 1}</td>
                    <td>{item.item_id}</td>
                    <td>{(item.score || 0).toFixed(4)}</td>
                  </tr>
                ))}
                {coverageData.length === 0 && (
                  <tr><td colSpan={3}>No coverage priorities found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Debug Queue */}
        <div className="section">
          <h2 className="section-title">Debug Queue</h2>
          {debugRepoDist.length > 0 && (
            <div style={{ height: '150px', marginBottom: '15px' }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={debugRepoDist} layout="vertical">
                  <XAxis type="number" />
                  <YAxis type="category" dataKey="repo" width={80} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#FFB7B6" stroke="black" strokeWidth={1} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="data-table">
            <table>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Item ID</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {debugData.slice(0, 20).map((item, idx) => (
                  <tr key={idx} onClick={() => fetchExplanation(item.item_id)} style={{ cursor: 'pointer' }}>
                    <td>{item.rank || idx + 1}</td>
                    <td style={{ maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.item_id}</td>
                    <td>{(item.score || 0).toFixed(4)}</td>
                  </tr>
                ))}
                {debugData.length === 0 && (
                  <tr><td colSpan={3}>No debug priorities found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Verification Plan Suggestions */}
      <div className="section" style={{ marginTop: '30px' }}>
        <h2 className="section-title">Verification Plan Suggestions</h2>
        <div className="data-table">
          <table>
            <thead>
              <tr>
                <th>Priority Type</th>
                <th>Item ID</th>
                <th>Score</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {planData.slice(0, 20).map((item, idx) => (
                <tr key={idx}>
                  <td>
                    <span style={{
                      padding: '2px 8px',
                      background: item.priority_type === 'failure' ? '#FB5E4C' :
                        item.priority_type === 'coverage' ? '#F7CC66' : '#9AD7FD',
                      border: '1px solid black',
                      fontWeight: '600',
                      fontSize: '11px'
                    }}>
                      {item.priority_type}
                    </span>
                  </td>
                  <td>{item.item_id}</td>
                  <td>{(item.score || 0).toFixed(4)}</td>
                  <td>{item.action}</td>
                </tr>
              ))}
              {planData.length === 0 && (
                <tr><td colSpan={4}>No plan items available</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Context Engine Proof */}
      <div className="section" style={{ marginTop: '30px' }}>
        <h2 className="section-title">Context Engine Proof (Score Decomposition)</h2>
        <div style={{ display: 'flex', gap: '20px' }}>
          <div style={{ flex: 1 }}>
            <div className="input-group">
              <label className="input-label">Select Module to Inspect</label>
              <select
                className="input-field"
                value={selectedModule ? `${selectedModule.repo}:${selectedModule.module_name}` : ''}
                onChange={(e) => {
                  const [repo, module] = e.target.value.split(':');
                  const found = contextData.find(m => m.repo === repo && m.module_name === module);
                  if (found) setSelectedModule(found);
                }}
              >
                {contextData.map((m, idx) => (
                  <option key={idx} value={`${m.repo}:${m.module_name}`}>
                    {m.repo}:{m.module_name} (score: {(m.context_score || 0).toFixed(1)})
                  </option>
                ))}
              </select>
            </div>
            {selectedModule && (
              <div style={{ height: '200px', marginTop: '15px' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={getScoreDecomposition(selectedModule)} layout="vertical">
                    <XAxis type="number" />
                    <YAxis type="category" dataKey="name" width={120} tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Bar dataKey="value" fill="#BE8EF8" stroke="black" strokeWidth={1} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
          <div style={{ width: '250px' }}>
            {selectedModule && (
              <div className="stat-card" style={{ height: 'auto', padding: '20px' }}>
                <div style={{ fontSize: '14px', fontWeight: '600', marginBottom: '15px' }}>Module Metrics</div>
                <div style={{ display: 'grid', gap: '10px' }}>
                  <div>
                    <div style={{ fontSize: '11px', color: '#666' }}>Context Score</div>
                    <div style={{ fontSize: '24px', fontWeight: '700' }}>{(selectedModule.context_score || 0).toFixed(2)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: '11px', color: '#666' }}>Risk Level</div>
                    <div style={{
                      fontSize: '16px',
                      fontWeight: '700',
                      color: selectedModule.risk_level === 'high' ? '#FB5E4C' :
                        selectedModule.risk_level === 'medium' ? '#D49800' : '#2E8B57'
                    }}>
                      {selectedModule.risk_level?.toUpperCase()}
                    </div>
                  </div>
                  {selectedModule.evidence && (
                    <>
                      <div>
                        <div style={{ fontSize: '11px', color: '#666' }}>Failure Rate</div>
                        <div style={{ fontSize: '16px', fontWeight: '600' }}>{selectedModule.evidence.failure_rate?.toFixed(1) || 0}%</div>
                      </div>
                      <div>
                        <div style={{ fontSize: '11px', color: '#666' }}>Coverage Gap</div>
                        <div style={{ fontSize: '16px', fontWeight: '600' }}>{selectedModule.evidence.coverage_gap?.toFixed(1) || 0}%</div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Explanations */}
      <div className="section" style={{ marginTop: '30px' }}>
        <h2 className="section-title">Explanations</h2>
        <p style={{ marginBottom: '15px', color: '#666' }}>Click on any item in the tables above to view its explanation</p>
        {selectedExplanation && (
          <div style={{
            background: 'white',
            border: '1px solid black',
            padding: '15px',
            fontFamily: 'monospace',
            fontSize: '12px',
            maxHeight: '300px',
            overflowY: 'auto',
            whiteSpace: 'pre-wrap'
          }}>
            {explanationData ? JSON.stringify(explanationData, null, 2) : 'Loading...'}
          </div>
        )}
        {!selectedExplanation && (
          <div style={{
            background: '#f5f5f5',
            border: '1px solid #ddd',
            padding: '20px',
            textAlign: 'center',
            color: '#666'
          }}>
            Select an item from Coverage Priorities or Debug Queue to view explanation
          </div>
        )}
      </div>
    </div>
  );
}

export default Dashboard;
