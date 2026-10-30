// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CopyIcon, EyeIcon, LockIcon } from "lucide-react"
import { type FormEvent, useState } from "react"
import { toast } from "sonner"

import { LoadError, PageHeader } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useSession } from "@/hooks/use-session"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

type Setting = Schemas["SettingView"]
type Draft = Record<string, unknown>

const GROUPS = [
  { title: "Behaviour", description: "What OnCue does on its own.", names: ["mode", "workers", "watched_folders", "watch_seconds", "audio_automatic"] },
  { title: "Backups", description: "How many saved versions each subtitle keeps.", names: ["keep_versions", "keep_days"] },
  { title: "Bazarr", description: "Where OnCue asks for subtitles and lists the library.", names: ["bazarr_url", "bazarr_api_key", "replacements"] },
  { title: "Notifications", description: "A message to a webhook when subtitles reach Review.", names: ["webhook_url", "webhook_minutes"] },
  { title: "Access", description: "How people log in and where the UI lives.", names: ["auth_mode", "url_base"] },
]
const LABELS: Record<string, string> = {
  bazarr_url: "Bazarr address", bazarr_api_key: "Bazarr API key", replacements: "Other subtitles to try", mode: "Mode", audio_automatic: "Write audio syncs on their own",
  watched_folders: "Watched folders", watch_seconds: "Watch interval (seconds)", workers: "Parallel checks", keep_versions: "Versions kept per subtitle",
  keep_days: "Keep every version for (days)", webhook_url: "Webhook address", webhook_minutes: "At most one message every (minutes)", auth_mode: "Log in", url_base: "URL base",
}
const CHOICES: Record<string, string> = { report: "Report only", fix: "Fix automatically", forms: "Always", local: "Not from private addresses", external: "A reverse proxy handles it" }
const KEY_PROMPTS = {
  show: { title: "Show the API key", description: "Enter your password to see the key.", confirm: "Show the key" },
  renew: { title: "Make a new API key?", description: "Scripts that use the current key stop working until they get the new one.", confirm: "Make a new key" },
} as const
type KeyPrompt = keyof typeof KEY_PROMPTS
const PASSWORD_SETTINGS = ["auth_mode", "url_base"]

export function SettingsPage() {
  const t = useT()
  const queryClient = useQueryClient()
  const session = useSession()
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => unwrap(api.GET("/api/settings")) })
  const [draft, setDraft] = useState<Draft>({})
  const [askingPassword, setAskingPassword] = useState(false)
  const [password, setPassword] = useState("")
  const closePrompt = () => {
    setAskingPassword(false)
    setPassword("")
  }
  const save = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/settings", { body: { changes: draft, password } })),
    onSuccess: (view) => {
      queryClient.setQueryData(["settings"], view)
      setDraft({})
      closePrompt()
      toast.success(t("Settings saved."))
    },
    onError: (error) => toast.error(error.message),
  })
  const startSave = () => {
    const needsPassword = Boolean(session.data?.login_required) && PASSWORD_SETTINGS.some((name) => name in draft)
    if (needsPassword) setAskingPassword(true)
    else save.mutate()
  }
  const submitPassword = (event: FormEvent) => {
    event.preventDefault()
    save.mutate()
  }
  if (settings.error) return <LoadError error={settings.error} onRetry={() => settings.refetch()} />
  if (!settings.data) return <Skeleton className="h-96 w-full" />
  const byName = Object.fromEntries(settings.data.settings.map((setting) => [setting.name, setting]))
  const changed = Object.keys(draft).length

  return (
    <>
      <PageHeader title={t("Settings")} description={t("A setting set in the environment always wins and shows as locked here.")} />
      <div className="space-y-4">
        {GROUPS.map((group) => (
          <Card key={group.title}>
            <CardHeader><CardTitle>{t(group.title)}</CardTitle><CardDescription>{t(group.description)}</CardDescription></CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              {group.names.map((name) => byName[name] && <Field key={name} setting={byName[name]!} draft={draft} setDraft={setDraft} />)}
            </CardContent>
          </Card>
        ))}
        <Card>
          <CardHeader><CardTitle>{t("Fixed by the environment")}</CardTitle><CardDescription>{t("These are never changed from the UI.")}</CardDescription></CardHeader>
          <CardContent className="space-y-3 text-sm">
            {settings.data.fixed.map((item) => (
              <div key={item.name} className="space-y-1">
                <div className="font-medium">{item.name === "allowed_folders" ? t("Allowed folders") : t("Automatic fixes need a confidence of")}</div>
                <div className="font-mono text-xs">{Array.isArray(item.value) ? (item.value.length ? item.value.join(", ") : t("Not set: the UI cannot change files until ONCUE_ROOTS is set")) : String(item.value)}</div>
                <div className="text-xs text-muted-foreground">{item.why}</div>
              </div>
            ))}
          </CardContent>
        </Card>
        <ApiKey />
      </div>
      {changed > 0 && (
        <div className="sticky bottom-4 z-10 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background/95 p-3 shadow-sm backdrop-blur" role="region" aria-label={t("Unsaved changes")}>
          <span className="text-sm">{changed > 1 ? t("{count} unsaved changes", { count: changed }) : t("{count} unsaved change", { count: changed })}</span>
          <div className="flex gap-2">
            <Button variant="ghost" disabled={save.isPending} onClick={() => setDraft({})}>{t("Discard")}</Button>
            <Button disabled={save.isPending} onClick={startSave}>{changed > 1 ? t("Save {count} changes", { count: changed }) : t("Save {count} change", { count: changed })}</Button>
          </div>
        </div>
      )}
      <Dialog open={askingPassword} onOpenChange={(open) => !open && closePrompt()}>
        <DialogContent>
          <form className="grid gap-4" onSubmit={submitPassword}>
            <DialogHeader>
              <DialogTitle>{t("Confirm with your password")}</DialogTitle>
              <DialogDescription>{t("Changing the log in or the URL base needs your password.")}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="settings-password">{t("Password")}</Label>
              <Input id="settings-password" type="password" autoComplete="current-password" autoFocus value={password} onChange={(event) => setPassword(event.target.value)} />
            </div>
            <DialogFooter>
              <DialogClose asChild><Button type="button" variant="outline">{t("Cancel")}</Button></DialogClose>
              <Button type="submit" disabled={save.isPending || !password}>{t("Save")}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}

function Field({ setting, draft, setDraft }: { setting: Setting; draft: Draft; setDraft: (update: (current: Draft) => Draft) => void }) {
  const t = useT()
  const value = setting.name in draft ? draft[setting.name] : setting.value
  const change = (next: unknown) => setDraft((current) => ({ ...current, [setting.name]: next }))
  const id = `setting-${setting.name}`
  const note = setting.locked ? t("Set by the environment.") : setting.restart ? t("Applies after a restart.") : setting.note
  if (setting.kind === "switch") {
    return (
      <div className="flex items-start justify-between gap-4 rounded-md border p-3 md:col-span-2">
        <div className="space-y-1">
          <Label htmlFor={id} className="flex items-center gap-1.5">{t(LABELS[setting.name] ?? setting.name)}{setting.locked && <LockIcon className="size-3 text-muted-foreground" />}</Label>
          {note && <p className="text-xs text-muted-foreground">{note}</p>}
        </div>
        <Switch id={id} checked={Boolean(value)} disabled={setting.locked} onCheckedChange={change} />
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="flex items-center gap-1.5">{t(LABELS[setting.name] ?? setting.name)}{setting.locked && <LockIcon className="size-3 text-muted-foreground" />}</Label>
      {setting.kind === "choice" && (
        <Select value={String(value)} disabled={setting.locked} onValueChange={change}>
          <SelectTrigger id={id} className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>{setting.choices.map((choice) => <SelectItem key={choice} value={choice}>{t(CHOICES[choice] ?? choice)}</SelectItem>)}</SelectContent>
        </Select>
      )}
      {setting.kind === "number" && <Input id={id} type="number" min={setting.low} max={setting.high} disabled={setting.locked} value={String(value)} onChange={(event) => change(Number(event.target.value))} />}
      {setting.kind === "folders" && (
        <textarea id={id} rows={3} disabled={setting.locked} value={Array.isArray(value) ? value.join("\n") : ""} placeholder="/data/media/movies"
          onChange={(event) => change(event.target.value.split("\n").map((line) => line.trim()).filter(Boolean))}
          className="w-full rounded-md border bg-transparent px-3 py-2 font-mono text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50" />
      )}
      {["url", "text", "path"].includes(setting.kind) && (
        <Input id={id} type={setting.kind === "text" && setting.name.includes("key") ? "password" : "text"} disabled={setting.locked}
          value={setting.value === null && !(setting.name in draft) ? "" : String(value ?? "")} placeholder={setting.value === null && setting.is_set ? t("Set, type to replace it") : ""}
          onChange={(event) => change(event.target.value)} />
      )}
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}

function ApiKey() {
  const t = useT()
  const session = useSession()
  const needsPassword = Boolean(session.data?.login_required)
  const [key, setKey] = useState("")
  const [prompt, setPrompt] = useState<KeyPrompt | null>(null)
  const [password, setPassword] = useState("")
  const close = () => {
    setPrompt(null)
    setPassword("")
  }
  const fetchKey = useMutation({
    mutationFn: (purpose: KeyPrompt) => unwrap(api.POST(purpose === "renew" ? "/api/settings/api-key" : "/api/settings/api-key/reveal", { body: { password } })),
    onSuccess: (result, purpose) => {
      setKey(result.key)
      close()
      if (purpose === "renew") toast.success(t("A new API key replaces the old one."))
    },
    onError: (error) => toast.error(error.message),
  })
  const show = () => (needsPassword ? setPrompt("show") : fetchKey.mutate("show"))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (prompt) fetchKey.mutate(prompt)
  }
  return (
    <Card>
      <CardHeader><CardTitle>{t("API key")}</CardTitle><CardDescription>{t("For scripts and Home Assistant: send it in the X-Api-Key header.")}</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap items-center gap-2">
        <Input readOnly aria-label={t("API key")} className="w-80 font-mono" value={key || "********************************"} />
        <Button variant="outline" size="icon" aria-label={t("Show the key")} disabled={fetchKey.isPending} onClick={show}><EyeIcon /></Button>
        <Button variant="outline" size="icon" aria-label={t("Copy the key")} disabled={!key} onClick={() => navigator.clipboard.writeText(key).then(() => toast.success(t("Copied.")))}><CopyIcon /></Button>
        <Button variant="outline" onClick={() => setPrompt("renew")}>{t("Make a new key")}</Button>
        <Dialog open={prompt !== null} onOpenChange={(open) => !open && close()}>
          <DialogContent>
            {prompt && (
              <form className="grid gap-4" onSubmit={submit}>
                <DialogHeader>
                  <DialogTitle>{t(KEY_PROMPTS[prompt].title)}</DialogTitle>
                  <DialogDescription>{t(KEY_PROMPTS[prompt].description)}</DialogDescription>
                </DialogHeader>
                {needsPassword && (
                  <div className="space-y-2">
                    <Label htmlFor="api-key-password">{t("Password")}</Label>
                    <Input id="api-key-password" type="password" autoComplete="current-password" autoFocus value={password} onChange={(event) => setPassword(event.target.value)} />
                  </div>
                )}
                <DialogFooter>
                  <DialogClose asChild><Button type="button" variant="outline">{t("Cancel")}</Button></DialogClose>
                  <Button type="submit" disabled={fetchKey.isPending || (needsPassword && !password)}>{t(KEY_PROMPTS[prompt].confirm)}</Button>
                </DialogFooter>
              </form>
            )}
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  )
}
