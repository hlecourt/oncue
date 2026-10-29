// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CopyIcon, EyeIcon, LockIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { PageHeader } from "@/components/status"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { api, type Schemas, unwrap } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

type Setting = Schemas["SettingView"]
type Draft = Record<string, unknown>

const GROUPS = [
  { title: "Behaviour", description: "What the guard does on its own.", names: ["mode", "workers", "watched_folders", "watch_seconds", "audio_automatic"] },
  { title: "Backups", description: "How many saved versions each subtitle keeps.", names: ["keep_versions", "keep_days"] },
  { title: "Bazarr", description: "Where the guard asks for subtitles and lists the library.", names: ["bazarr_url", "bazarr_api_key", "replacements"] },
  { title: "Notifications", description: "A message to a webhook when subtitles reach Review.", names: ["webhook_url", "webhook_minutes"] },
  { title: "Access", description: "How people log in and where the UI lives.", names: ["auth_mode", "url_base"] },
]
const LABELS: Record<string, string> = {
  bazarr_url: "Bazarr address", bazarr_api_key: "Bazarr API key", replacements: "Other subtitles to try", mode: "Mode", audio_automatic: "Write audio syncs on their own",
  watched_folders: "Watched folders", watch_seconds: "Watch interval (seconds)", workers: "Parallel checks", keep_versions: "Versions kept per subtitle",
  keep_days: "Keep every version for (days)", webhook_url: "Webhook address", webhook_minutes: "At most one message every (minutes)", auth_mode: "Log in", url_base: "URL base",
}
const CHOICES: Record<string, string> = { report: "Report only", fix: "Fix automatically", forms: "Always", local: "Not from private addresses", external: "A reverse proxy handles it" }

export function SettingsPage() {
  const t = useT()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => unwrap(api.GET("/api/settings")) })
  const [draft, setDraft] = useState<Draft>({})
  const save = useMutation({
    mutationFn: () => unwrap(api.PATCH("/api/settings", { body: { changes: draft } })),
    onSuccess: (view) => {
      queryClient.setQueryData(["settings"], view)
      setDraft({})
      toast.success(t("Settings saved."))
    },
    onError: (error) => toast.error(error.message),
  })
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
                <div className="font-mono text-xs">{Array.isArray(item.value) ? (item.value.length ? item.value.join(", ") : t("Not set: the UI cannot change files until SUBTITLE_GUARD_ROOTS is set")) : String(item.value)}</div>
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
            <Button disabled={save.isPending} onClick={() => save.mutate()}>{changed > 1 ? t("Save {count} changes", { count: changed }) : t("Save {count} change", { count: changed })}</Button>
          </div>
        </div>
      )}
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
        <textarea id={id} rows={3} disabled={setting.locked} value={(value as string[]).join("\n")} placeholder="/data/media/movies"
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
  const queryClient = useQueryClient()
  const [shown, setShown] = useState(false)
  const key = useQuery({ queryKey: ["api-key"], queryFn: () => unwrap(api.GET("/api/settings/api-key")), enabled: shown })
  const renew = useMutation({
    mutationFn: () => unwrap(api.POST("/api/settings/api-key")),
    onSuccess: (result) => {
      queryClient.setQueryData(["api-key"], result)
      setShown(true)
      toast.success(t("A new API key replaces the old one."))
    },
  })
  return (
    <Card>
      <CardHeader><CardTitle>{t("API key")}</CardTitle><CardDescription>{t("For scripts and Home Assistant: send it in the X-Api-Key header.")}</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap items-center gap-2">
        <Input readOnly aria-label={t("API key")} className="w-80 font-mono" value={shown ? key.data?.key ?? "" : "********************************"} />
        <Button variant="outline" size="icon" aria-label={t("Show the key")} onClick={() => setShown(true)}><EyeIcon /></Button>
        <Button variant="outline" size="icon" aria-label={t("Copy the key")} disabled={!key.data} onClick={() => navigator.clipboard.writeText(key.data!.key).then(() => toast.success(t("Copied.")))}><CopyIcon /></Button>
        <AlertDialog>
          <AlertDialogTrigger asChild><Button variant="outline">{t("Make a new key")}</Button></AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("Make a new API key?")}</AlertDialogTitle>
              <AlertDialogDescription>{t("Scripts that use the current key stop working until they get the new one.")}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter><AlertDialogCancel>{t("Cancel")}</AlertDialogCancel><AlertDialogAction onClick={() => renew.mutate()}>{t("Make a new key")}</AlertDialogAction></AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </CardContent>
    </Card>
  )
}
