import { useState, useEffect, useCallback } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
  ChartData,
} from 'chart.js'
import { Bar } from 'react-chartjs-2'
import { Line } from 'react-chartjs-2'
import './App.css'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
)

const STORAGE_KEY = 'api_key'

interface ScoreBucket {
  range: string
  count: number
}

interface ScoreResponse {
  lab_id: string
  buckets: ScoreBucket[]
}

interface TimelinePoint {
  date: string
  count: number
}

interface TimelineResponse {
  lab_id: string
  submissions: TimelinePoint[]
}

interface PassRateEntry {
  task_id: string
  task_name: string
  pass_rate: number
  total_submissions: number
}

interface PassRatesResponse {
  lab_id: string
  tasks: PassRateEntry[]
}

interface LabOption {
  id: string
  label: string
}

const LABS: LabOption[] = [
  { id: 'lab-04', label: 'Lab 04' },
  { id: 'lab-05', label: 'Lab 05' },
]

type DashboardData = {
  scores: ScoreResponse | null
  timeline: TimelineResponse | null
  passRates: PassRatesResponse | null
}

type FetchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: DashboardData }
  | { status: 'error'; message: string }

function buildChartData(buckets: ScoreBucket[]): ChartData<'bar'> {
  return {
    labels: buckets.map((b) => b.range),
    datasets: [
      {
        label: 'Submissions',
        data: buckets.map((b) => b.count),
        backgroundColor: 'rgba(54, 162, 235, 0.5)',
        borderColor: 'rgba(54, 162, 235, 1)',
        borderWidth: 1,
      },
    ],
  }
}

function buildTimelineData(points: TimelinePoint[]): ChartData<'line'> {
  return {
    labels: points.map((p) => p.date),
    datasets: [
      {
        label: 'Submissions per day',
        data: points.map((p) => p.count),
        borderColor: 'rgba(75, 192, 192, 1)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
        tension: 0.1,
        fill: true,
      },
    ],
  }
}

async function fetchWithToken<T>(
  url: string,
  token: string,
  params: Record<string, string>,
): Promise<T> {
  const query = new URLSearchParams(params).toString()
  const fullUrl = `${url}?${query}`

  const res = await fetch(fullUrl, {
    headers: { Authorization: `Bearer ${token}` },
  })

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }

  return res.json() as Promise<T>
}

function Dashboard() {
  const [labId, setLabId] = useState<string>(LABS[0].id)
  const [fetchState, setFetchState] = useState<FetchState>({ status: 'idle' })

  const fetchData = useCallback(async () => {
    const token = localStorage.getItem(STORAGE_KEY)
    if (!token) {
      setFetchState({ status: 'error', message: 'No API token found' })
      return
    }

    setFetchState({ status: 'loading' })

    try {
      const [scores, timeline, passRates] = await Promise.all([
        fetchWithToken<ScoreResponse>('/analytics/scores', token, {
          lab: labId,
        }),
        fetchWithToken<TimelineResponse>('/analytics/timeline', token, {
          lab: labId,
        }),
        fetchWithToken<PassRatesResponse>('/analytics/pass-rates', token, {
          lab: labId,
        }),
      ])

      setFetchState({
        status: 'success',
        data: { scores, timeline, passRates },
      })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      setFetchState({ status: 'error', message })
    }
  }, [labId])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  function handleLabChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setLabId(e.target.value)
  }

  return (
    <div>
      <header className="app-header">
        <h1>Dashboard</h1>
        <div>
          <label htmlFor="lab-select">Lab: </label>
          <select id="lab-select" value={labId} onChange={handleLabChange}>
            {LABS.map((lab) => (
              <option key={lab.id} value={lab.id}>
                {lab.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {fetchState.status === 'loading' && <p>Loading dashboard...</p>}

      {fetchState.status === 'error' && (
        <p className="error">Error: {fetchState.message}</p>
      )}

      {fetchState.status === 'success' && (
        <div className="dashboard-grid">
          {/* Score Distribution Bar Chart */}
          {fetchState.data.scores && fetchState.data.scores.buckets.length > 0 && (
            <div className="chart-card">
              <h2>Score Distribution</h2>
              <canvas
                data-testid="score-bar-chart"
                style={{ maxWidth: '600px', margin: '0 auto' }}
              />
              <Bar
                data={buildChartData(fetchState.data.scores.buckets)}
                options={{
                  responsive: true,
                  plugins: {
                    legend: { display: false },
                    title: {
                      display: true,
                      text: 'Score Distribution',
                    },
                  },
                }}
              />
            </div>
          )}

          {/* Submissions Timeline Line Chart */}
          {fetchState.data.timeline &&
            fetchState.data.timeline.submissions.length > 0 && (
              <div className="chart-card">
                <h2>Submissions Over Time</h2>
                <canvas
                  data-testid="timeline-line-chart"
                  style={{ maxWidth: '600px', margin: '0 auto' }}
                />
                <Line
                  data={buildTimelineData(fetchState.data.timeline.submissions)}
                  options={{
                    responsive: true,
                    plugins: {
                      legend: { display: false },
                      title: {
                        display: true,
                        text: 'Submissions per Day',
                      },
                    },
                    scales: {
                      x: {
                        ticks: {
                          maxRotation: 45,
                          minRotation: 45,
                        },
                      },
                    },
                  }}
                />
              </div>
            )}

          {/* Pass Rates Table */}
          {fetchState.data.passRates &&
            fetchState.data.passRates.tasks.length > 0 && (
              <div className="chart-card">
                <h2>Pass Rates per Task</h2>
                <table>
                  <thead>
                    <tr>
                      <th>Task</th>
                      <th>Pass Rate</th>
                      <th>Total Submissions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fetchState.data.passRates.tasks.map((task) => (
                      <tr key={task.task_id}>
                        <td>{task.task_name}</td>
                        <td>{(task.pass_rate * 100).toFixed(1)}%</td>
                        <td>{task.total_submissions}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      )}
    </div>
  )
}

export default Dashboard
