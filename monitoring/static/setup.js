/* The parent's SDK post wrapper carries the active user's authenticated session. */
Vue.component('nightjar-setup', {
  props: ['view', 'form', 'post'],
  data() {
    return { busy: false, clientError: '', teamName: '', login1: '', login2: '', monitorId: null,
      existingTeam: null, assignments: {}, files: [], workspaceId: null, projectName: '', kind: 'images',
      metaText: '', selectedDatasets: [], selectedTeams: [], replicas: 1, batchUnits: '', imageUnits: '',
      pilot: true, withAnnotations: false, progress: '', inspectionNote: '',
      monitorGroupName: 'Monitors', annotatorGroupName: 'Annotators', existingMonitorGroup: null, existingAnnotatorGroup: null,
      monitorLogins: '', annotatorLogins: '', candidateMonitorIds: [] };
  },
  computed: {
    catalog() { return this.view.catalog || {}; },
    setup() { return this.view.setup || {}; },
    roster() { return this.setup.roster || []; },
    groups() { return this.setup.groups || {ready: false, monitors: [], annotators: []}; },
    monitors() { return this.groups.ready ? this.groups.monitors : (this.catalog.monitors || []); },
    unpairedAnnotators() {
      const paired = new Set(this.roster.flatMap(t => t.annotator_ids));
      return (this.groups.annotators || []).filter(u => !paired.has(u.id));
    },
    unassignedPairs() { return this.roster.filter(t => !t.monitor_id); },
    participantTeams() { return (this.catalog.remote_teams || []).filter(t => ![this.groups.monitoring_team_id, this.groups.annotator_team_id].includes(t.id)); },
    readyTeams() { return this.roster.filter(t => !t.has_batches && t.monitor_id); },
    blocked() { return !!this.setup.blocked_by; },
    preview() { return this.view.preview; },
    accept() { return this.kind === 'images' ? '.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff' : '.mp4,.avi,.mov,.mkv,.webm'; }
  },
  watch: { busy(value) { this.$emit('busy', value); } },
  methods: {
    id() { return crypto.randomUUID().replace(/-/g, ''); },
    monitorName(id) { return this.monitors.find(u => u.id === id)?.login || 'User ' + id; },
    async request(action, values = {}) {
      Object.assign(this.form, values, { setup_action: action });
      try {
        const result = await this.post('/setup/action');
        if (result?.ok === false || this.view.error) throw new Error(this.view.message || 'Setup request failed.');
      } finally { delete this.form.chunk; }
    },
    async run(action, values = {}) {
      if (this.busy) return;
      this.busy = true; this.clientError = '';
      try { await this.request(action, values); }
      catch (e) { this.clientError = e.message; }
      finally { this.busy = false; }
    },
    createGroups() {
      return this.run('groups', {operation_id: this.id(), monitor_group_name: this.monitorGroupName,
        annotator_group_name: this.annotatorGroupName, existing_monitor_group: this.existingMonitorGroup,
        existing_annotator_group: this.existingAnnotatorGroup});
    },
    async addMembers(group) {
      const text = group === 'monitors' ? this.monitorLogins : this.annotatorLogins;
      await this.run('members', {operation_id: this.id(), member_group: group,
        member_logins: text.split(/\r?\n/).map(v => v.trim()).filter(Boolean)});
      if (!this.clientError) this[group === 'monitors' ? 'monitorLogins' : 'annotatorLogins'] = '';
    },
    appendLogin(group, login) {
      if (!login) return;
      const key = group === 'monitors' ? 'monitorLogins' : 'annotatorLogins';
      const list = this[key].split(/\r?\n/).filter(Boolean);
      if (!list.includes(login)) this[key] = [...list, login].join('\n');
    },
    async createTeam() {
      await this.run('team', { operation_id: this.id(), name: this.teamName,
        logins: [this.login1, this.login2], monitor_id: this.monitorId, existing_team_id: this.existingTeam });
      if (!this.clientError) { this.teamName = ''; this.login1 = ''; this.login2 = ''; this.monitorId = null; this.existingTeam = null; }
    },
    assign(t) {
      return this.run('assign', { operation_id: this.id(), setup_team_id: t.id,
        monitor_id: this.assignments[t.id] || t.monitor_id, setup_revision: t.revision });
    },
    filesChanged(e) { this.files = Array.from(e.target.files || []); },
    async metaChanged(e) {
      this.clientError = '';
      try { this.metaText = e.target.files[0] ? await e.target.files[0].text() : ''; JSON.parse(this.metaText || '{}'); }
      catch (error) { this.clientError = 'Select a valid Supervisely meta.json file.'; }
    },
    async upload() {
      if (this.busy || !this.files.length) return;
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
  mounted() { this.run('catalog'); },
  template: `
  <section class="nj-setup">
    <div class="nj-section-heading"><div><h2>Event setup</h2><p class="nj-muted">Prepare teams and work for monitors. Annotators use the Supervisely web app for labeling.</p></div>
      <button @click="run('catalog')" :disabled="busy">Reload Supervisely</button></div>
    <div v-if="clientError" class="nj-message nj-error" role="alert">{{ clientError }}</div>
    <div v-if="blocked" class="nj-panel nj-caution">
      <h3>Setup operation {{ setup.blocked_by }}</h3><p>Another operation is running, or its remote result needs inspection. Check the operation history below.</p>
      <template v-if="(setup.operations || []).some(o => o.id === setup.blocked_by && o.state === 'needs_inspection')">
        <label>What did you verify in Supervisely?<textarea v-model="inspectionNote" rows="2" maxlength="2000"></textarea></label>
        <button @click="run('inspect', {operation_id: setup.blocked_by, inspection_note: inspectionNote})"
          :disabled="busy || !inspectionNote.trim()">Record inspection and unblock setup</button>
      </template>
    </div>
    <div v-if="!groups.ready" class="nj-panel"><h3>1. Create the event groups</h3>
      <p class="nj-muted">Create one group for monitors and one for annotators. The app keeps their Supervisely IDs for you.</p>
      <div class="nj-columns"><div>
        <label class="nj-field">Monitors group name<input v-model="monitorGroupName" maxlength="180"></label>
        <label v-if="view.connection && view.connection.local" class="nj-field">Supervisely team<select v-model="existingMonitorGroup"><option :value="null">Create a new monitors group</option>
          <option v-for="t in catalog.remote_teams || []" :key="t.id" :value="t.id">Use {{ t.name }}</option></select></label>
        <p v-else class="nj-muted">The monitors group uses the team where this shared app session is running.</p>
      </div><div>
        <label class="nj-field">Annotators group name<input v-model="annotatorGroupName" maxlength="180"></label>
        <label class="nj-field">Supervisely team<select v-model="existingAnnotatorGroup"><option :value="null">Create a new annotators group</option>
          <option v-for="t in catalog.remote_teams || []" :key="t.id" :value="t.id">Use {{ t.name }}</option></select></label>
      </div></div>
      <button class="nj-primary" @click="createGroups" :disabled="busy || blocked || !monitorGroupName.trim() || !annotatorGroupName.trim()">Create event groups</button>
    </div>
    <div v-if="groups.ready" class="nj-columns">
      <div v-for="group in ['monitors', 'annotators']" :key="group" class="nj-panel">
        <h3>{{ group === 'monitors' ? 'Monitors' : 'Annotators' }} group · {{ groups[group].length }} people</h3>
        <p class="nj-muted">{{ group === 'monitors' ? 'Monitors use this app to oversee their assigned pairs.' : 'Annotators work in Supervisely labeling jobs. This roster does not give them monitoring-app access.' }}</p>
        <p class="nj-muted">Choose registered users below or enter their exact logins, one per line.</p>
        <select aria-label="Choose a registered user" @change="appendLogin(group, $event.target.value); $event.target.value = ''"><option value="">Choose a registered user</option>
          <option v-for="u in catalog.users || []" :key="u.id" :value="u.login">{{ u.name }} · {{ u.login }}</option></select>
        <label v-if="group === 'monitors'" class="nj-field">Monitor logins<textarea v-model="monitorLogins" rows="3" placeholder="One login per line"></textarea></label>
        <label v-else class="nj-field">Annotator logins<textarea v-model="annotatorLogins" rows="3" placeholder="One login per line"></textarea></label>
        <div class="nj-actions"><button @click="addMembers(group)" :disabled="busy || blocked || !(group === 'monitors' ? monitorLogins : annotatorLogins).trim()">Add people to group</button>
          <button v-if="group === 'monitors' && view.connection.login" @click="appendLogin(group, view.connection.login)">Include my account</button></div>
        <div class="nj-contribution" v-for="u in groups[group]" :key="u.id"><span>{{ u.name }} · {{ u.login }}</span>
          <button @click="run('member_remove', {operation_id: id(), member_user_id: u.id})" :disabled="busy || blocked">Remove</button></div>
      </div>
    </div>
    <div v-if="groups.ready">
      <div class="nj-panel"><h3>2. Create a participant pair</h3>
        <p class="nj-muted">Choose two unpaired people from the Annotators group. Assign a monitor now or distribute pairs in the next step.</p>
        <label class="nj-field">Team name<input v-model="teamName" maxlength="180" placeholder="Your pilot team name"></label>
        <label class="nj-field">Participant team<select v-model="existingTeam"><option :value="null">Create a new Supervisely team</option>
          <option v-for="t in participantTeams" :key="t.id" :value="t.id">Use existing: {{ t.name }} · {{ t.id }}</option></select></label>
        <div class="nj-form-row"><label>First annotator<select v-model="login1"><option value="">Choose an annotator</option>
          <option v-for="u in unpairedAnnotators" :key="u.id" :value="u.login" :disabled="u.login === login2">{{ u.name }} · {{ u.login }}</option></select></label>
          <label>Second annotator<select v-model="login2"><option value="">Choose an annotator</option>
          <option v-for="u in unpairedAnnotators" :key="u.id" :value="u.login" :disabled="u.login === login1">{{ u.name }} · {{ u.login }}</option></select></label></div>
        <label class="nj-field">Assigned monitor<select v-model="monitorId"><option :value="null">Assign later</option>
          <option v-for="u in monitors" :key="u.id" :value="u.id">{{ u.login }} · {{ u.id }}</option></select></label>
        <p class="nj-muted">The assigned monitor receives reviewer/manager access to this pair. Unassigned pairs stay out of data distribution.</p>
        <button class="nj-primary" @click="createTeam" :disabled="busy || blocked || !teamName.trim() || !login1.trim() || !login2.trim()">Create participant pair</button>
      </div>
    </div>
    <div v-if="roster.length" class="nj-panel"><h3>3. Distribute pairs to monitors</h3>
      <p v-if="unassignedPairs.length">{{ unassignedPairs.length }} pairs need a monitor.</p>
      <div v-if="unassignedPairs.length" class="nj-allocation">
        <p class="nj-muted">Select monitors for a balanced proposal, or assign each pair manually below.</p>
        <label v-for="m in monitors" :key="m.id" class="nj-check"><input type="checkbox" v-model="candidateMonitorIds" :value="m.id">{{ m.name }} · {{ m.login }}</label>
        <button @click="run('balance_preview', {monitor_ids: candidateMonitorIds})" :disabled="busy || blocked || !candidateMonitorIds.length">Preview balanced assignments</button>
        <div v-if="view.assignment_plan_id"><p v-for="row in view.assignment_preview" :key="row.team_id">{{ row.team_name }} → {{ monitorName(row.monitor_id) }}</p>
          <button class="nj-primary" @click="run('balance_apply', {assignment_plan_id: view.assignment_plan_id})" :disabled="busy || blocked">Apply monitor assignments</button></div>
      </div>
      <div class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Participant team</th><th>Annotators</th><th>Monitor</th><th></th></tr></thead>
        <tbody><tr v-for="t in roster" :key="t.id"><td>{{ t.name }} · {{ t.id }}</td><td>{{ t.annotator_ids.join(' / ') }}</td>
          <td><select :value="assignments[t.id] || t.monitor_id" @change="$set(assignments, t.id, Number($event.target.value))">
            <option :value="0" disabled>Unassigned</option><option v-for="u in monitors" :key="u.id" :value="u.id">{{ u.login }} · {{ u.id }}</option></select></td>
          <td><button @click="assign(t)" :disabled="busy || blocked || !(assignments[t.id] || t.monitor_id)">Save assignment</button></td></tr></tbody></table></div>
      <p class="nj-muted">Assignments control what each monitor can see in this app. Reassign before jobs are released. Existing Supervisely memberships remain unchanged.</p>
    </div>
    <div v-if="groups.ready" class="nj-panel"><h3>4. Upload source data</h3>
        <p class="nj-muted">Upload files here, or use an existing dataset from the monitoring team in step 5. Keep images and videos in separate projects.</p>
        <label class="nj-field">Workspace<select v-model="workspaceId"><option :value="null">Choose a monitoring workspace</option>
          <option v-for="w in catalog.workspaces || []" :key="w.id" :value="w.id">{{ w.name }}</option></select></label>
        <div class="nj-form-row"><label>Source project name<input v-model="projectName" maxlength="180"></label>
          <label>Media type<select v-model="kind" @change="files = []; if ($refs.mediaFiles) $refs.mediaFiles.value = ''">
            <option value="images">Images</option><option value="video">Videos</option></select></label></div>
        <label class="nj-field">Files<input ref="mediaFiles" type="file" multiple :accept="accept" @change="filesChanged"></label>
        <p class="nj-muted">{{ files.length }} files selected. Files are transferred in chunks; keep this tab open until upload completes.</p>
        <label class="nj-field">Annotation schema (optional meta.json)<input type="file" accept=".json" @change="metaChanged"></label>
        <p class="nj-muted">Without meta.json, the pilot template adds a bird box, crown, left_eye, right_eye and beak points, bird_id and visibility tags. Give the box and its points the same bird_id. You can edit the source schema in Supervisely before distribution.</p>
        <button class="nj-primary" @click="upload" :disabled="busy || blocked || !workspaceId || !projectName.trim() || !files.length">Upload dataset to Supervisely</button>
        <p v-if="progress" role="status">{{ progress }}</p>
      </div>
    <div v-if="groups.ready" class="nj-panel"><h3>5. Preview data distribution</h3>
      <p class="nj-muted">Choose your actual teams and source inventory. A pilot can use fewer than three replicas and can contain images only.</p>
      <div class="nj-columns">
        <div><h4>Source datasets</h4><label v-for="d in catalog.datasets || []" :key="d.id" class="nj-check">
          <input type="checkbox" v-model="selectedDatasets" :value="d.id"> {{ d.name }} ({{ d.kind }})</label>
          <p v-if="!(catalog.datasets || []).length">Upload data above, then reload Supervisely.</p></div>
        <div><h4>Participant teams</h4><label v-for="t in readyTeams" :key="t.id" class="nj-check">
          <input type="checkbox" v-model="selectedTeams" :value="t.id"> {{ t.name }} · {{ monitorName(t.monitor_id) }}</label>
          <p v-if="!readyTeams.length">Register teams first. Teams with existing batches are excluded.</p></div>
      </div>
      <label class="nj-check"><input type="checkbox" v-model="pilot"> Small pilot (allow reduced replication and images-only work)</label>
      <div class="nj-form-row"><label>Independent teams per source<input v-model.number="replicas" type="number" :min="pilot ? 1 : 3"></label>
        <label>Estimated effort units per image<input v-model.number="imageUnits" type="number" min="1" placeholder="Enter your pilot estimate"></label>
        <label>Maximum effort units per image batch<input v-model.number="batchUnits" type="number" min="1" placeholder="Choose a batch size"></label></div>
      <p class="nj-muted">Videos use their actual frame counts as effort units and stay complete. Use a comparable scale for image effort. The planner checks that every video has enough independent teams, with at most one final video per team.</p>
      <label class="nj-check"><input type="checkbox" v-model="withAnnotations"> Copy existing source annotations (otherwise start with blank labels)</label>
      <button @click="plan" :disabled="busy || blocked || !selectedDatasets.length || !selectedTeams.length || !imageUnits || !batchUnits">Preview allocation</button>
    </div>
    <div v-if="preview && view.plan_id" class="nj-panel"><h3>Review allocation {{ preview.pilot ? '(pilot)' : '(full event)' }}</h3>
      <p>{{ preview.source_images }} source images · {{ preview.source_videos }} source videos · {{ preview.replicas }} requested independent teams per source</p>
      <div class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Team</th><th>Monitor</th><th>Images</th><th>Videos</th><th>Batches</th><th>Effort units</th></tr></thead>
        <tbody><tr v-for="t in preview.teams" :key="t.id"><td>{{ t.name }}</td><td>{{ monitorName(t.monitor_id) }}</td><td>{{ t.images }}</td><td>{{ t.videos }}</td><td>{{ t.batches }}</td><td>{{ t.units }}</td></tr></tbody></table></div>
      <p class="nj-muted">Apply creates team-local projects and independent annotation entities. Labeling jobs stay locked until the assigned monitor releases them.</p>
      <button class="nj-primary" @click="run('distribute', {plan_id: view.plan_id})" :disabled="busy || blocked">Apply distribution to Supervisely</button>
    </div>
    <div v-if="(setup.operations || []).length" class="nj-panel"><h3>Setup operation history</h3>
      <div v-for="o in setup.operations" :key="o.id" class="nj-operation"><strong>{{ o.kind }} · {{ o.state }}</strong><small>{{ o.id }}</small>
        <p v-if="o.result.error" class="nj-caution">{{ o.result.error }}</p>
        <details v-if="o.result.resources"><summary>Created resource IDs</summary><pre>{{ JSON.stringify(o.result.resources, null, 2) }}</pre></details>
      </div>
    </div>
  </section>`
});
