import { createRoot } from 'react-dom/client'
import { WorkspacePickerDialog } from '@/components/media/WorkspacePicker'
import { mediaApi, type MediaAsset } from '@/lib/media'

export function pickMediaFile(accept: string): Promise<MediaAsset | null> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) { resolve(null); return }
      try {
        resolve(await mediaApi.upload(file))
      } catch (cause) {
        reject(cause)
      }
    }
    input.addEventListener('cancel', () => resolve(null), { once: true })
    input.click()
  })
}

export function pickWorkspaceDirectory(suggestedName = 'New project'): Promise<string | null> {
  return new Promise((resolve) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const finish = (reference: string | null) => {
      root.unmount()
      host.remove()
      resolve(reference)
    }
    root.render(
      <WorkspacePickerDialog
        suggestedName={suggestedName}
        onCancel={() => finish(null)}
        onSelect={(directory) => finish(directory.reference)}
      />,
    )
  })
}
