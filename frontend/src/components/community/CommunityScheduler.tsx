import { useEffect, useRef } from 'react'
import { communityApi } from '@/lib/api'
import { loadRoxyConfig } from '@/lib/roxy'
import { taskStore } from '@/store/task.store'


const POLL_INTERVAL_MS = 30000


export default function CommunityScheduler() {
  const runningRef = useRef(false)

  useEffect(() => {
    let stopped = false

    const tick = async () => {
      if (stopped || runningRef.current) return
      const roxy = loadRoxyConfig()
      if (!roxy.apiHost.trim() || !roxy.apiToken.trim()) return

      runningRef.current = true
      try {
        const due = await communityApi.due()
        for (const post of due.posts.slice(0, 3)) {
          const taskId = taskStore.add({
            toolId: 'children',
            toolLabel: 'Community Scheduler',
            label: `Auto-publish community post #${post.id}`,
          })
          try {
            const res = await communityApi.publishNow(post.id, {
              api_host: roxy.apiHost,
              api_token: roxy.apiToken,
            })
            taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
          } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Auto-publish failed.'
            taskStore.complete(taskId, 'error', message)
          }
        }
      } catch {
        // Keep the scheduler silent unless a task actually ran.
      } finally {
        runningRef.current = false
      }
    }

    tick()
    const timer = window.setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [])

  return null
}
