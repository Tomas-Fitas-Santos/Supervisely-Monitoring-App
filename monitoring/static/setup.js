/* The parent's SDK post wrapper carries the active user's authenticated session. */
Vue.component('nightjar-management', {
  props: ['view', 'form', 'post', 'section', 'locked'],
  data() {
    return { busy: false, clientError: '', teamName: '', login1: '', login2: '', monitorId: null,
      existingTeam: null, assignments: {}, files: [], workspaceId: null, projectName: '', kind: 'images',
      metaText: '', selectedDatasets: [], selectedTeams: [], replicas: 1, batchUnits: '', imageUnits: '',
      pilot: true, withAnnotations: false, progress: '', inspectionNote: '',
      monitorGroupName: 'Monitors', annotatorGroupName: 'Annotators', existingMonitorGroup: null, existingAnnotatorGroup: null,
      monitorLogins: '', annotatorLogins: '', candidateMonitorIds: [],
      selectedMonitor: null, monitorSearch: '', teamSearch: '', addTeamToMonitor: null, showAddMonitor: false, showAddTeam: false, loaded: false };
  },
  computed: {
    catalog() { return this.view.catalog || {}; },
    setup() { return this.view.setup || {}; },
    directory() { return this.view.directory || {monitors: [], teams: [], annotators: []}; },
    roster() { return this.directory.teams || []; },
    groups() { return this.setup.groups || {ready: this.directory.ready, monitors: this.directory.monitors || [], annotators: this.directory.annotators || []}; },
    monitors() { return this.directory.monitors || []; },
    filteredMonitors() { return this.monitors.filter(m => (m.name + ' ' + m.login).toLowerCase().includes(this.monitorSearch.toLowerCase())); },
    filteredTeams() { return this.roster.filter(t => (t.name + ' ' + (t.participants || []).map(u => u.name + ' ' + u.login).join(' ')).toLowerCase().includes(this.teamSearch.toLowerCase())); },
    monitor() { return this.monitors.find(m => m.id === this.selectedMonitor) || null; },
    assignedTeams() { return this.roster.filter(t => t.monitor_id === this.selectedMonitor); },
    unassignedPairs() { return this.roster.filter(t => !t.monitor_id); },
    assignableTeams() { return this.roster.filter(t => t.can_assign && t.monitor_id !== this.selectedMonitor); },
    participantTeams() { return (this.catalog.remote_teams || []).filter(t => ![this.groups.monitoring_team_id, this.groups.annotator_team_id].includes(t.id)); },
    readyTeams() { return this.roster.filter(t => !t.has_batches && t.monitor_id); },
    blocked() { return !!this.setup.blocked_by; },
    disabled() { return this.busy || this.locked || this.blocked; },
    sources() { return this.view.sources || {datasets: [], workspaces: []}; },
    suggestedParticipants() {
      const paired = new Set(this.roster.flatMap(t => t.annotator_ids));
      const monitors = new Set(this.monitors.map(u => u.id));
      return (this.catalog.users || []).filter(u => !paired.has(u.id) && !monitors.has(u.id) && u.login !== this.view.connection.login);
    },
    preview() { return this.view.preview; },
    accept() { return this.kind === 'images' ? '.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff' : '.mp4,.avi,.mov,.mkv,.webm'; }
  },
  watch: { busy(value) { this.$emit('busy', value); },
    locked(value) { if (!value && (!this.loaded || (this.view.can_setup && !Array.isArray(this.catalog.users)))) this.reload(); },
    'groups.workspace_id'(value) { if (!this.workspaceId && value) this.workspaceId = value; } },
  methods: {
    id() { return crypto.randomUUID().replace(/-/g, ''); },
    monitorName(id) { return id ? (this.monitors.find(u => u.id === id)?.name || 'Monitor ' + id) : 'Unassigned'; },
    participants(t) { return (t.participants || []).map(u => u.name + ' (' + u.login + ')').join(' · '); },
    chooseMonitor(m) { this.selectedMonitor = m.id; this.addTeamToMonitor = null; },
    assignedCount(m) { return this.roster.filter(t => t.monitor_id === m.id).length; },
    async reload() { const ok = await this.run(this.view.can_setup ? 'catalog' : 'overview'); if (ok) this.loaded = true; },
    async addToMonitor() { const t = this.roster.find(t => t.id === this.addTeamToMonitor); if (t && this.monitor) { this.$set(this.assignments, t.id, this.monitor.id); await this.assign(t); if (!this.clientError) this.addTeamToMonitor = null; } },
    removeFromMonitor(t) { return this.run('unassign', {operation_id: this.id(), setup_team_id: t.id, setup_revision: t.revision, expected_monitor_id: t.monitor_id}).then(ok => { if (ok) this.$delete(this.assignments, t.id); return ok; }); },
    async request(action, values = {}) {
      Object.assign(this.form, values, { setup_action: action });
      try {
        const result = await this.post('/setup/action');
        if (result?.ok === false || this.view.error) throw new Error(this.view.message || 'Setup request failed.');
      } finally { delete this.form.chunk; }
    },
    async run(action, values = {}) {
      if (this.busy || this.locked) return false;
      this.busy = true; this.clientError = '';
      try { await this.request(action, values); return true; }
      catch (e) { this.clientError = e.message; return false; }
      finally { this.busy = false; }
    },
    createGroups() {
      return this.run('groups', {operation_id: this.id(), monitor_group_name: this.monitorGroupName,
        annotator_group_name: this.annotatorGroupName, existing_monitor_group: this.existingMonitorGroup,
        existing_annotator_group: this.existingAnnotatorGroup});
    },
    async addMembers(group) {
      const text = group === 'monitors' ? this.monitorLogins : this.annotatorLogins;
      const ok = await this.run('members', {operation_id: this.id(), member_group: group,
        member_logins: text.split(/\r?\n/).map(v => v.trim()).filter(Boolean)});
      if (ok) { this.showAddMonitor = false; this[group === 'monitors' ? 'monitorLogins' : 'annotatorLogins'] = ''; }
    },
    appendLogin(group, login) {
      if (!login) return;
      const key = group === 'monitors' ? 'monitorLogins' : 'annotatorLogins';
      const list = this[key].split(/\r?\n/).filter(Boolean);
      if (!list.includes(login)) this[key] = [...list, login].join('\n');
    },
    async createTeam() {
      const ok = await this.run('register_team', { operation_id: this.id(), name: this.teamName,
        logins: [this.login1, this.login2], monitor_id: this.monitorId, existing_team_id: this.existingTeam });
      if (ok) { this.showAddTeam = false; this.teamName = ''; this.login1 = ''; this.login2 = ''; this.monitorId = null; this.existingTeam = null; }
    },
    assign(t) {
      const chosen = Object.prototype.hasOwnProperty.call(this.assignments, t.id) ? this.assignments[t.id] : t.monitor_id;
      if (!chosen) return this.removeFromMonitor(t);
      return this.run('assign', { operation_id: this.id(), setup_team_id: t.id,
        monitor_id: chosen, setup_revision: t.revision }).then(ok => { if (ok) this.$delete(this.assignments, t.id); });
    },
    filesChanged(e) { this.files = Array.from(e.target.files || []); },
    async metaChanged(e) {
      this.clientError = '';
      try { this.metaText = e.target.files[0] ? await e.target.files[0].text() : ''; JSON.parse(this.metaText || '{}'); }
      catch (error) { this.clientError = 'Select a valid Supervisely meta.json file.'; }
    },
    async upload() {
      if (this.disabled || !this.files.length) return;
      this.busy = true; this.clientError = '';
      try {
        const meta = this.metaText.trim() ? JSON.parse(this.metaText) : null;
        const uploadIds = [];
        for (const file of this.files) {
          const uploadId = this.id();
          for (let offset = 0; offset < file.size; offset += 4 * 1024 * 1024) {
            this.progress = 'Uploading ' + file.name + ' · ' + Math.floor(offset / file.size * 100) + '%';
            const blob = file.slice(offset, offset + 4 * 1024 * 1024);
            const encoded = await new Promise((resolve, reject) => {
              const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]);
              reader.onerror = reject; reader.readAsDataURL(blob);
            });
            await this.request('chunk', { upload_id: uploadId, filename: file.name,
              size: file.size, offset, chunk: encoded });
          }
          uploadIds.push(uploadId);
        }
        this.progress = 'Importing files into Supervisely…';
        await this.request('upload', { operation_id: this.id(), upload_ids: uploadIds,
          workspace_id: this.workspaceId, name: this.projectName, kind: this.kind, meta });
        this.progress = 'Dataset uploaded. Select it for distribution below.';
        this.files = []; if (this.$refs.mediaFiles) this.$refs.mediaFiles.value = '';
      } catch (e) { this.clientError = e.message; this.progress = ''; }
      finally { this.busy = false; }
    },
    plan() {
      return this.run('preview', { dataset_ids: this.selectedDatasets, team_ids: this.selectedTeams,
        replicas: Number(this.replicas), batch_units: Number(this.batchUnits), image_units: Number(this.imageUnits),
        pilot: this.pilot, with_annotations: this.withAnnotations });
    }
  },
  mounted() { if (!this.locked) this.reload(); if (this.groups.workspace_id) this.workspaceId = this.groups.workspace_id; },
  template: `
  <section class="nj-management">
    <div class="nj-section-heading"><div><h2>{{ section === 'roster' ? 'Monitors & Teams' : 'Dataset' }}</h2>
      <p class="nj-muted">{{ section === 'roster' ? 'Register monitors and participant pairs, then assign each team to a monitor.' : 'Upload event source data, choose teams and review distribution before applying it.' }}</p></div>
      <button @click="reload" :disabled="busy || locked">Reload lists</button></div>
    <p v-if="!view.can_setup" class="nj-message">You can view the event lists. The monitoring organiser manages people, assignments and dataset distribution.</p>
    <div v-if="clientError" class="nj-message nj-error" role="alert">{{ clientError }}</div>
    <div v-if="blocked && view.can_setup" class="nj-panel nj-caution"><h3>Setup needs attention</h3><p>Check the operation history at the bottom of this tab before continuing.</p>
      <template v-if="(setup.operations || []).some(o => o.id === setup.blocked_by && o.state === 'needs_inspection')">
        <label>What did you verify in Supervisely?<textarea v-model="inspectionNote" rows="2" maxlength="2000"></textarea></label>
        <button @click="run('inspect', {operation_id: setup.blocked_by, inspection_note: inspectionNote})" :disabled="busy || locked || !inspectionNote.trim()">Record inspection and continue</button>
      </template></div>
    <div v-if="!directory.ready" class="nj-panel"><h3>Start the event</h3>
      <p>Create the event groups once. Then add monitors and teams here; manage source data in Dataset. Annotators work in the Supervisely web app.</p>
      <template v-if="view.can_setup && section === 'roster'">
        <details><summary>Choose existing Supervisely groups or change their names</summary><div class="nj-columns">
          <div><label class="nj-field">Monitors group name<input v-model="monitorGroupName" maxlength="180"></label>
            <label v-if="view.connection.local" class="nj-field">Monitors Supervisely team<select v-model="existingMonitorGroup"><option :value="null">Create a new team</option><option v-for="t in catalog.remote_teams || []" :key="t.id" :value="t.id">{{ t.name }}</option></select></label>
            <p v-else class="nj-muted">Monitors use the team where this shared session is running.</p></div>
          <div><label class="nj-field">Annotators group name<input v-model="annotatorGroupName" maxlength="180"></label>
            <label class="nj-field">Annotators Supervisely team<select v-model="existingAnnotatorGroup"><option :value="null">Create a new team</option><option v-for="t in catalog.remote_teams || []" :key="t.id" :value="t.id">{{ t.name }}</option></select></label></div></div></details>
        <button class="nj-primary" @click="createGroups" :disabled="disabled || !monitorGroupName.trim() || !annotatorGroupName.trim()">Initialize event</button>
      </template><p v-else>Open Monitors & Teams as the organiser to initialize the event.</p></div>
    <template v-if="section === 'roster'">
      <div class="nj-metrics nj-three-metrics"><div><span>Monitors</span><strong>{{ monitors.length }}</strong></div><div><span>Participant teams</span><strong>{{ roster.length }}</strong></div><div><span>Teams without a monitor</span><strong>{{ unassignedPairs.length }}</strong></div></div>
      <div v-if="directory.ready && view.can_setup" class="nj-actions nj-toolbar"><button class="nj-primary" @click="showAddMonitor = !showAddMonitor" :disabled="disabled">Add monitor</button><button class="nj-primary" @click="showAddTeam = !showAddTeam" :disabled="disabled">Add team</button></div>
      <div v-if="showAddMonitor && view.can_setup" class="nj-panel"><h3>Add monitors</h3><p>Choose a registered user or enter registered logins, one per line.</p>
        <select aria-label="Choose a registered monitor" @change="appendLogin('monitors', $event.target.value); $event.target.value = ''"><option value="">Choose a registered user</option><option v-for="u in catalog.users || []" :key="u.id" :value="u.login">{{ u.name }} · {{ u.login }}</option></select>
        <label class="nj-field">Monitor logins<textarea v-model="monitorLogins" rows="3"></textarea></label>
        <div class="nj-actions"><button class="nj-primary" @click="addMembers('monitors')" :disabled="disabled || !monitorLogins.trim()">Save monitors</button><button v-if="view.connection.login" @click="appendLogin('monitors', view.connection.login)">Include my account</button><button @click="showAddMonitor = false" :disabled="busy">Cancel</button></div></div>
      <div v-if="showAddTeam && view.can_setup" class="nj-panel"><h3>Add a team and its two participants</h3>
        <label class="nj-field">Team name<input v-model="teamName" maxlength="180"></label>
        <datalist id="nj-participant-logins"><option v-for="u in suggestedParticipants" :key="u.id" :value="u.login">{{ u.name }}</option></datalist>
        <div class="nj-form-row"><label>First participant's Supervisely login<input v-model="login1" list="nj-participant-logins" autocomplete="off"></label><label>Second participant's Supervisely login<input v-model="login2" list="nj-participant-logins" autocomplete="off"></label></div>
        <p class="nj-muted">Both must already have Supervisely accounts. The app registers them in the event and adds them to this team. They will annotate in Supervisely.</p>
        <label class="nj-field">Monitor<select v-model="monitorId"><option :value="null">Unassigned — assign later</option><option v-for="u in monitors" :key="u.id" :value="u.id">{{ u.name }} · {{ u.login }}</option></select></label>
        <details><summary>Use an existing participant team</summary><label class="nj-field">Supervisely team<select v-model="existingTeam"><option :value="null">Create a new Supervisely team</option><option v-for="t in participantTeams" :key="t.id" :value="t.id">{{ t.name }}</option></select></label></details>
        <div class="nj-actions"><button class="nj-primary" @click="createTeam" :disabled="disabled || !teamName.trim() || !login1.trim() || !login2.trim() || login1.toLowerCase() === login2.toLowerCase()">Save team</button><button @click="showAddTeam = false" :disabled="busy">Cancel</button></div></div>
      <div class="nj-directory-layout"><aside class="nj-panel"><h3>Monitors</h3><input v-model="monitorSearch" placeholder="Find a monitor" aria-label="Find a monitor">
        <button v-for="m in filteredMonitors" :key="m.id" class="nj-team" :class="{selected: selectedMonitor === m.id}" @click="chooseMonitor(m)"><span>{{ m.name }}</span><small>{{ m.login }} · {{ assignedCount(m) }} teams</small></button>
        <p v-if="!monitors.length" class="nj-muted">No monitors registered. Add the people who will oversee this event.</p></aside>
        <div class="nj-panel"><template v-if="monitor"><div class="nj-section-heading"><div><h3>{{ monitor.name }}</h3><p class="nj-muted">{{ monitor.login }} · {{ assignedTeams.length }} assigned teams</p></div><button v-if="view.can_setup" @click="run('member_remove', {operation_id: id(), member_user_id: monitor.id})" :disabled="disabled || assignedTeams.length > 0">Remove monitor</button></div>
          <div v-for="t in assignedTeams" :key="t.id" class="nj-assignment-card"><div><strong>{{ t.name }}</strong><p class="nj-muted">{{ participants(t) }}</p></div><button v-if="view.can_setup" @click="removeFromMonitor(t)" :disabled="disabled || !t.can_assign">Remove team from monitor</button></div>
          <p v-if="!assignedTeams.length">No teams assigned to this monitor.</p>
          <div v-if="view.can_setup" class="nj-actions"><select v-model="addTeamToMonitor" aria-label="Team to assign to selected monitor"><option :value="null">Choose a team to assign</option><option v-for="t in assignableTeams" :key="t.id" :value="t.id">{{ t.name }} · {{ monitorName(t.monitor_id) }}</option></select><button class="nj-primary" @click="addToMonitor" :disabled="disabled || !addTeamToMonitor">Assign team to monitor</button></div>
          <p class="nj-muted">Assignments can be edited before jobs are released. Released jobs need a reviewer handover.</p>
        </template><p v-else>Select a monitor to see their assigned teams and manage assignments.</p></div></div>
      <div class="nj-panel"><div class="nj-section-heading"><h3>All participant teams</h3><input v-model="teamSearch" placeholder="Find a team or participant" aria-label="Find a team or participant"></div>
        <div class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Team</th><th>Participants</th><th>Assigned monitor</th><th v-if="view.can_setup">Change assignment</th></tr></thead><tbody>
          <tr v-for="t in filteredTeams" :key="t.id"><td><strong>{{ t.name }}</strong></td><td>{{ participants(t) }}</td><td><span class="nj-badge" :class="{warning: !t.monitor_id}">{{ monitorName(t.monitor_id) }}</span></td>
            <td v-if="view.can_setup"><div class="nj-inline-actions"><select :value="Object.prototype.hasOwnProperty.call(assignments, t.id) ? assignments[t.id] : t.monitor_id" @change="$set(assignments, t.id, Number($event.target.value))" :disabled="disabled || !t.can_assign"><option :value="0">Unassigned</option><option v-for="m in monitors" :key="m.id" :value="m.id">{{ m.name }}</option></select><button @click="assign(t)" :disabled="disabled || !t.can_assign || !Object.prototype.hasOwnProperty.call(assignments, t.id) || assignments[t.id] === t.monitor_id">Save</button></div></td></tr></tbody></table></div>
        <p v-if="!filteredTeams.length" class="nj-muted">No teams found. Use Add team to register a pair and its participants.</p></div>
      <details v-if="unassignedPairs.length && view.can_setup" class="nj-panel"><summary>Assign unassigned teams evenly across selected monitors</summary><label v-for="m in monitors" :key="m.id" class="nj-check"><input type="checkbox" v-model="candidateMonitorIds" :value="m.id">{{ m.name }}</label>
        <button @click="run('balance_preview', {monitor_ids: candidateMonitorIds})" :disabled="disabled || !candidateMonitorIds.length">Preview balanced assignments</button>
        <div v-if="view.assignment_plan_id"><p v-for="row in view.assignment_preview" :key="row.team_id">{{ row.team_name }} → {{ monitorName(row.monitor_id) }}</p><button class="nj-primary" @click="run('balance_apply', {assignment_plan_id: view.assignment_plan_id})" :disabled="disabled">Apply monitor assignments</button></div></details>
    </template>
    <template v-if="section === 'dataset' && directory.ready">
      <div class="nj-panel"><h3>Event source datasets</h3><p class="nj-muted">These are the source files. Distribution creates independent annotation copies for the selected participant teams.</p>
        <div class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Project / dataset</th><th>Type</th><th>Files</th></tr></thead><tbody><tr v-for="d in sources.datasets" :key="d.id"><td>{{ d.project }} / {{ d.name }}</td><td>{{ d.kind === 'images' ? 'Images' : 'Videos' }}</td><td>{{ d.items }}</td></tr></tbody></table></div><p v-if="!(sources.datasets || []).length">No source data yet. Upload files below, or import a dataset into the monitoring team in Supervisely and reload this list.</p></div>
      <div v-if="view.can_setup" class="nj-panel"><h3>Upload dataset</h3><p class="nj-muted">Upload raw images or videos. Select a group of files of the same type.</p>
        <label class="nj-field">Source workspace<select v-model="workspaceId"><option :value="null">Choose a workspace</option><option v-for="w in sources.workspaces || []" :key="w.id" :value="w.id">{{ w.name }}</option></select></label>
        <div class="nj-form-row"><label>Project name<input v-model="projectName" maxlength="180"></label><label>Media type<select v-model="kind" @change="files = []; if ($refs.mediaFiles) $refs.mediaFiles.value = ''"><option value="images">Images</option><option value="video">Videos</option></select></label></div>
        <label class="nj-field">Files<input ref="mediaFiles" type="file" multiple :accept="accept" @change="filesChanged"></label><p>{{ files.length }} files selected.</p>
        <details><summary>Annotation schema</summary><label class="nj-field">Optional Supervisely meta.json<input type="file" accept=".json" @change="metaChanged"></label><p class="nj-muted">Without a schema file, uploads use the bird box and four head-point pilot template. Edit the source schema in Supervisely before distribution if needed.</p></details>
        <button class="nj-primary" @click="upload" :disabled="disabled || !workspaceId || !projectName.trim() || !files.length">Upload to Supervisely</button><p v-if="progress" role="status">{{ progress }}</p></div>
      <div v-if="view.can_setup" class="nj-panel"><h3>Distribute dataset to teams</h3><div class="nj-columns"><div><h4>1. Choose source datasets</h4><label v-for="d in sources.datasets || []" :key="d.id" class="nj-check"><input type="checkbox" v-model="selectedDatasets" :value="d.id">{{ d.project }} / {{ d.name }} ({{ d.items }} files)</label></div>
        <div><h4>2. Choose participant teams</h4><label v-for="t in readyTeams" :key="t.id" class="nj-check"><input type="checkbox" v-model="selectedTeams" :value="t.id">{{ t.name }} · {{ monitorName(t.monitor_id) }}</label><p v-if="!readyTeams.length">Add teams and assign their monitors in Monitors & Teams. Teams already given batches are excluded.</p></div></div>
        <h4>3. Choose workload and replication</h4><label class="nj-check"><input type="checkbox" v-model="pilot">Small pilot (allow reduced replication and images-only work)</label>
        <div class="nj-form-row"><label>Independent teams per source<input v-model.number="replicas" type="number" :min="pilot ? 1 : 3"></label><label>Estimated effort units per image<input v-model.number="imageUnits" type="number" min="1" placeholder="Your estimate"></label><label>Maximum effort units per image batch<input v-model.number="batchUnits" type="number" min="1" placeholder="Choose batch size"></label></div>
        <p class="nj-muted">Videos remain complete and use actual frame counts as effort units. Enter a comparable image-effort estimate.</p><label class="nj-check"><input type="checkbox" v-model="withAnnotations">Copy existing source annotations</label>
        <button @click="plan" :disabled="disabled || !selectedDatasets.length || !selectedTeams.length || !imageUnits || !batchUnits">Preview distribution</button></div>
      <div v-if="preview && view.plan_id && view.can_setup" class="nj-panel"><h3>Review dataset distribution</h3><p>{{ preview.source_images }} source images · {{ preview.source_videos }} source videos · {{ preview.replicas }} independent teams per source</p><div class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Team</th><th>Monitor</th><th>Images</th><th>Videos</th><th>Batches</th></tr></thead><tbody><tr v-for="t in preview.teams" :key="t.id"><td>{{ t.name }}</td><td>{{ monitorName(t.monitor_id) }}</td><td>{{ t.images }}</td><td>{{ t.videos }}</td><td>{{ t.batches }}</td></tr></tbody></table></div><button class="nj-primary" @click="run('distribute', {plan_id: view.plan_id})" :disabled="disabled">Apply distribution</button><p class="nj-muted">Jobs will be released from Monitoring by each assigned monitor.</p></div>
      <div class="nj-panel"><h3>Distribution status by team</h3><div v-for="t in roster" :key="t.id" class="nj-contribution"><span>{{ t.name }} · {{ monitorName(t.monitor_id) }}</span><span class="nj-badge" :class="{warning: !t.monitor_id}">{{ t.has_batches ? 'Batches prepared' : (t.monitor_id ? 'Awaiting distribution' : 'Assign a monitor first') }}</span></div><p v-if="!roster.length">No teams registered yet.</p></div>
    </template>
    <details v-if="view.can_setup && (setup.operations || []).length" class="nj-panel"><summary>Operation history and recovery</summary><div v-for="o in setup.operations" :key="o.id" class="nj-operation"><strong>{{ o.kind }} · {{ o.state }}</strong><small>{{ o.id }}</small><p v-if="o.result.error" class="nj-caution">{{ o.result.error }}</p><details v-if="o.result.resources"><summary>Confirmed resources</summary><pre>{{ JSON.stringify(o.result.resources, null, 2) }}</pre></details></div></details>
  </section>`
});
