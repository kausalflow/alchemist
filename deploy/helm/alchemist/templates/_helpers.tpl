{{- define "alchemist.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "alchemist.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "alchemist.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "alchemist.serviceAccount" -}}
{{- if .Values.serviceAccount.create -}}{{ default (include "alchemist.fullname" .) .Values.serviceAccount.name }}
{{- else -}}{{ default "default" .Values.serviceAccount.name }}{{- end -}}
{{- end -}}

{{- define "alchemist.image" -}}
{{ .Values.image.repository }}:{{ default .Chart.AppVersion .Values.image.tag }}
{{- end -}}

{{- define "alchemist.localUrl" -}}
{{- if .Values.models.localUrl -}}{{ .Values.models.localUrl }}
{{- else if .Values.ollama.enabled -}}http://{{ include "alchemist.fullname" . }}-ollama:11434/v1{{- end -}}
{{- end -}}

{{/* Environment, config files and security settings every Alchemist container shares. */}}
{{- define "alchemist.container" -}}
image: {{ include "alchemist.image" . }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
securityContext: {{- toYaml .Values.securityContext | nindent 2 }}
envFrom:
  - configMapRef:
      name: {{ include "alchemist.fullname" . }}-env
  {{- if .Values.existingSecret }}
  - secretRef:
      name: {{ .Values.existingSecret }}
  {{- end }}
{{- with .Values.env }}
env:
  {{- range $k, $v := . }}
  - name: {{ $k }}
    value: {{ $v | quote }}
  {{- end }}
{{- end }}
volumeMounts:
  - {name: config, mountPath: /config, readOnly: true}
  - {name: data, mountPath: /data}
  {{- if .Values.knowledge.existingConfigMap }}
  - {name: knowledge, mountPath: /knowledge, readOnly: true}
  {{- end }}
{{- end -}}

{{/* Call with (dict "root" $ "persistent" true|false). */}}
{{- define "alchemist.volumes" -}}
- name: config
  configMap:
    name: {{ include "alchemist.fullname" .root }}-config
- name: data
  {{- if .persistent }}
  persistentVolumeClaim:
    claimName: {{ include "alchemist.fullname" .root }}-data
  {{- else }}
  emptyDir: {}
  {{- end }}
{{- if .root.Values.knowledge.existingConfigMap }}
- name: knowledge
  configMap:
    name: {{ .root.Values.knowledge.existingConfigMap }}
{{- end }}
{{- end -}}
