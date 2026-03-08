import React, { useState, useEffect } from 'react';
import { getContext, generateVerificationPlan } from '../services/api';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';

const PRIORITY_COLORS = {
  failure: '#FB5E4C',
  coverage: '#F7CC66',
  debug: '#98E4C1',
};

function VerificationPlan({ runId, featureRunId, onBack }) {
  const [planItems, setPlanItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    fetchData();
  }, [runId, featureRunId]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await getContext(runId);
      const items = data.items || [];

      // Build verification plan from context
      const plan = [];

      // Top failure risks
      items
        .filter(i => i.risk_level === 'high')
        .slice(0, 5)
        .forEach((item, idx) => {
          plan.push({
            id: `F${idx + 1}`,
            type: 'failure',
            module: item.module_name,
            repo: item.repo,
            score: item.context_score,
            action: `Investigate high-risk module ${item.module_name}`,
            status: 'pending',
          });
        });

      // Coverage priorities
      items
        .filter(i => (i.coverage_gap || 0) > 30)
        .slice(0, 5)
        .forEach((item, idx) => {
          plan.push({
            id: `C${idx + 1}`,
            type: 'coverage',
            module: item.module_name,
            repo: item.repo,
            score: item.coverage_gap || 50,
            action: `Close coverage gap for ${item.module_name}`,
            status: 'pending',
          });
        });

      // Debug items (from failure signals)
      items
        .filter(i => i.failure_rate > 30)
        .slice(0, 5)
        .forEach((item, idx) => {
          plan.push({
            id: `D${idx + 1}`,
            type: 'debug',
            module: item.module_name,
            repo: item.repo,
            score: item.failure_rate,
            action: `Debug failures in ${item.module_name}`,
            status: 'pending',
          });
        });

      setPlanItems(plan.sort((a, b) => b.score - a.score));
    } catch (error) {
      console.error('Failed to generate plan:', error);
      setMockData();
    }
    setLoading(false);
  };

  const setMockData = () => {
    setPlanItems([
      { id: 'F1', type: 'failure', module: 'aes_core', repo: 'opentitan', score: 92, action: 'Investigate high-risk module aes_core - multiple timeout failures detected', status: 'pending' },
      { id: 'F2', type: 'failure', module: 'hmac', repo: 'opentitan', score: 87, action: 'Review HMAC verification tests - digest mismatch issues reported', status: 'pending' },
      { id: 'C1', type: 'coverage', module: 'decoder', repo: 'ibex', score: 82, action: 'Close coverage gap for decoder - 52% coverage remaining', status: 'in_progress' },
      { id: 'D1', type: 'debug', module: 'mmu', repo: 'cva6', score: 78, action: 'Debug TLB lookup timeout failures in mmu', status: 'pending' },
      { id: 'C2', type: 'coverage', module: 'alu', repo: 'veer_eh1', score: 72, action: 'Add tests for ALU edge cases - overflow handling', status: 'pending' },
      { id: 'F3', type: 'failure', module: 'prefetch', repo: 'ibex', score: 68, action: 'Investigate prefetch buffer underflow warnings', status: 'completed' },
      { id: 'D2', type: 'debug', module: 'scoreboard', repo: 'cva6', score: 65, action: 'Debug issue queue assertion failures', status: 'pending' },
      { id: 'C3', type: 'coverage', module: 'lsu', repo: 'veer_eh1', score: 58, action: 'Increase LSU alignment test coverage', status: 'pending' },
    ]);
  };

  const handleGeneratePlan = async () => {
    setGenerating(true);
    try {
      const result = await generateVerificationPlan(runId, featureRunId, []);
      if (result.items) {
        setPlanItems(result.items);
      }
    } catch (error) {
      console.error('Failed to generate plan:', error);
    }
    setGenerating(false);
  };

  const updateStatus = (id, newStatus) => {
    setPlanItems(items =>
      items.map(item =>
        item.id === id ? { ...item, status: newStatus } : item
      )
    );
  };

  // Stats
  const typeDistribution = planItems.reduce((acc, p) => {
    acc[p.type] = (acc[p.type] || 0) + 1;
    return acc;
  }, {});

  const statusDistribution = planItems.reduce((acc, p) => {
    acc[p.status] = (acc[p.status] || 0) + 1;
    return acc;
  }, {});

  const pieData = Object.entries(typeDistribution).map(([type, count]) => ({
    name: (type || '').charAt(0).toUpperCase() + (type || '').slice(1),
    value: count,
    color: PRIORITY_COLORS[type] || '#BE8EF8',
  }));

  if (loading) {
    return (
      <div className="main-container">
        <div className="loading">Generating verification plan...</div>
      </div>
    );
  }

  return (
    <div className="main-container">
      <div className="page-header">
        <h1 className="page-title">Verification Plan</h1>
        <button className="back-btn" onClick={onBack}>← Go Back</button>
      </div>

      <p style={{ marginBottom: '20px', fontSize: '16px' }}>
        AI-generated verification plan combining failure risks, coverage priorities, and debug queue items into actionable tasks.
      </p>

      {/* Stats */}
      <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(5, 1fr)' }}>
        <div className="stat-card">
          <div className="stat-value">{planItems.length}</div>
          <div className="stat-label">Total Tasks</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{typeDistribution.failure || 0}</div>
          <div className="stat-label">Failure Tasks</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{typeDistribution.coverage || 0}</div>
          <div className="stat-label">Coverage Tasks</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{typeDistribution.debug || 0}</div>
          <div className="stat-label">Debug Tasks</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{statusDistribution.completed || 0}</div>
          <div className="stat-label">Completed</div>
        </div>
      </div>

      <div className="two-columns">
        {/* Distribution Chart */}
        <div className="chart-container">
          <h3 className="chart-title">Task Distribution</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={90}
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

        {/* Progress Overview */}
        <div className="chart-container">
          <h3 className="chart-title">Progress Overview</h3>
          <div style={{ padding: '20px' }}>
            <div style={{ marginBottom: '20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
                <span>Overall Progress</span>
                <span>{statusDistribution.completed || 0} / {planItems.length}</span>
              </div>
              <div className="progress-bar">
                <div
                  className="progress-fill"
                  style={{
                    width: `${planItems.length > 0 ? ((statusDistribution.completed || 0) / planItems.length * 100) : 0}%`,
                    background: '#98E4C1'
                  }}
                ></div>
              </div>
            </div>

            <div style={{ display: 'grid', gap: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px', background: '#FEE3D2', borderRadius: '5px' }}>
                <span>🔴 Pending</span>
                <strong>{statusDistribution.pending || 0}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px', background: '#F7CC66', borderRadius: '5px' }}>
                <span>🟡 In Progress</span>
                <strong>{statusDistribution.in_progress || 0}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px', background: '#98E4C1', borderRadius: '5px' }}>
                <span>🟢 Completed</span>
                <strong>{statusDistribution.completed || 0}</strong>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Action Button */}
      <div style={{ margin: '20px 0', textAlign: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={handleGeneratePlan}
          disabled={generating}
          style={{ fontSize: '18px', padding: '15px 30px' }}
        >
          {generating ? 'Generating...' : '🔄 Regenerate Plan'}
        </button>
      </div>

      {/* Plan Tasks */}
      <h2 className="section-title">Verification Tasks</h2>
      <div style={{ display: 'grid', gap: '15px' }}>
        {planItems.map((item, idx) => (
          <div
            key={item.id}
            style={{
              background: 'white',
              borderRadius: '5px',
              border: '1px solid black',
              boxShadow: '4px 4px 0px black',
              padding: '20px',
              display: 'grid',
              gridTemplateColumns: '60px 1fr auto',
              gap: '20px',
              alignItems: 'center',
              opacity: item.status === 'completed' ? 0.7 : 1,
            }}
          >
            {/* Priority Badge */}
            <div style={{
              width: '50px',
              height: '50px',
              borderRadius: '50%',
              background: PRIORITY_COLORS[item.type] || '#BE8EF8',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              fontSize: '18px',
              border: '2px solid black',
            }}>
              #{idx + 1}
            </div>

            {/* Task Details */}
            <div>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '8px' }}>
                <span style={{
                  padding: '4px 10px',
                  borderRadius: '15px',
                  background: PRIORITY_COLORS[item.type],
                  fontSize: '12px',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                }}>
                  {item.type}
                </span>
                <span style={{ fontSize: '14px', color: '#666' }}>
                  {item.repo} / {item.module}
                </span>
                <span style={{ fontSize: '14px', fontWeight: 600 }}>
                  Score: {(item.score || 0).toFixed(0)}
                </span>
              </div>
              <div style={{ fontSize: '16px', fontWeight: 500 }}>
                {item.action || 'No action specified'}
              </div>
            </div>

            {/* Status Actions */}
            <div style={{ display: 'flex', gap: '10px' }}>
              <select
                value={item.status}
                onChange={(e) => updateStatus(item.id, e.target.value)}
                style={{
                  padding: '8px 15px',
                  borderRadius: '5px',
                  border: '1px solid black',
                  fontFamily: 'Syne, sans-serif',
                  fontSize: '14px',
                  background: item.status === 'completed' ? '#98E4C1'
                    : item.status === 'in_progress' ? '#F7CC66'
                      : 'white',
                }}
              >
                <option value="pending">Pending</option>
                <option value="in_progress">In Progress</option>
                <option value="completed">Completed</option>
              </select>
            </div>
          </div>
        ))}
      </div>

      {/* Export */}
      <div style={{ marginTop: '30px', display: 'flex', gap: '15px', justifyContent: 'flex-end' }}>
        <button
          className="btn btn-secondary"
          onClick={() => {
            const csv = planItems.map((p, i) =>
              `${i + 1},${p.type},${p.repo},${p.module},${p.score},${p.status},"${p.action}"`
            ).join('\n');
            const blob = new Blob([`priority,type,repo,module,score,status,action\n${csv}`], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `verification_plan_${runId}.csv`;
            a.click();
          }}
        >
          Export CSV
        </button>
        <button
          className="btn btn-primary"
          onClick={() => window.print()}
        >
          Print Plan
        </button>
      </div>
    </div>
  );
}

export default VerificationPlan;
