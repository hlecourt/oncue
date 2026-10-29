// SPDX-License-Identifier: GPL-3.0-or-later
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ShieldCheckIcon } from "lucide-react"
import { type FormEvent, useState } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SESSION_KEY } from "@/hooks/use-session"
import { api, rememberCsrf, unwrap } from "@/lib/api/client"
import { useT } from "@/lib/i18n"

export function LoginPage() {
  const t = useT()
  const queryClient = useQueryClient()
  const [user, setUser] = useState("admin")
  const [password, setPassword] = useState("")
  const login = useMutation({
    mutationFn: () => unwrap(api.POST("/api/session", { body: { user, password } })),
    onSuccess: (state) => {
      rememberCsrf(state.csrf)
      queryClient.setQueryData(SESSION_KEY, state)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    login.mutate()
  }

  return (
    <main className="flex min-h-svh items-center justify-center bg-muted/40 p-4">
      <div className="w-full max-w-sm space-y-4">
        <div className="flex items-center justify-center gap-2 font-semibold">
          <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <ShieldCheckIcon className="size-4" />
          </span>
          subtitle-guard
        </div>
        <Card>
          <CardHeader>
            <h1 className="leading-none font-semibold">{t("Log in")}</h1>
            <CardDescription>{t("Use the account of this subtitle-guard.")}</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              {login.error && (
                <Alert variant="destructive" id="login-problem" role="alert">
                  <AlertDescription>{login.error.message}</AlertDescription>
                </Alert>
              )}
              <div className="space-y-2">
                <Label htmlFor="user">{t("User name")}</Label>
                <Input id="user" autoComplete="username" value={user} onChange={(event) => setUser(event.target.value)}
                  aria-invalid={Boolean(login.error)} aria-describedby={login.error ? "login-problem" : undefined} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">{t("Password")}</Label>
                <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={Boolean(login.error)} aria-describedby={login.error ? "login-problem" : undefined} />
              </div>
              <Button type="submit" className="w-full" disabled={login.isPending || !password}>
                {login.isPending ? t("Logging in...") : t("Log in")}
              </Button>
            </form>
          </CardContent>
        </Card>
        <Collapsible className="text-center text-sm text-muted-foreground">
          <CollapsibleTrigger className="underline-offset-4 hover:underline">{t("How do I set the password?")}</CollapsibleTrigger>
          <CollapsibleContent className="mt-2 text-left">
            {t("Set SUBTITLE_GUARD_UI_USER and SUBTITLE_GUARD_UI_PASSWORD, or run subtitle-guard set-password. On a first start without one, a password for admin is printed once in the service logs.")}
          </CollapsibleContent>
        </Collapsible>
      </div>
    </main>
  )
}
