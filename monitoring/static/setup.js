/* The parent's SDK post wrapper carries the active user's authenticated session. */
Vue.component('nightjar-setup', {
  props: ['view', 'form', 'post'],
  data() {
    return { busy: false, clientError: '', teamName: '', login1: '', login2: '', monitorId: null,
      existingTeam: null, assignments: {}, files: [], workspaceId: null, projectName: '', kind: 'images',
      metaText: '', selectedDatasets: [], selectedTeams: [], replicas: 1, batchUnits: '', imageUnits: '',
      pilot: true, withAnnotations: false, progress: '', inspectionNote: '' };
  },
  computed: {
    catalog() { return this.view.catalog || {}; },
    setup() { return this.view.setup || {}; },
    roster() { return this.setup.roster || []; },
    monitors() { return this.catalog.monitors || []; },
    readyTeams() { return this.roster.filter(t => !t.has_batches); },
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
    createTeam() {
      return this.run('team', { operation_id: this.id(), name: this.teamName,
        logins: [this.login1, this.login2], monitor_id: this.monitorId, existing_team_id: this.existingTeam });
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
    <div class="nj-section-heading"><div><h2>Event setup</h2><p class="nj-muted">Create a real pilot using registered Supervisely accounts and your own data.</p></div>
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
    <div class="nj-columns">
      <div class="nj-panel"><h3>1. Register a participant pair</h3>
        <p class="nj-muted">Enter exact logins or choose users you can access. New accounts are not created.</p>
        <label class="nj-field">Team name<input v-model="teamName" maxlength="180" placeholder="Your pilot team name"></label>
        <label class="nj-field">Participant team<select v-model="existingTeam"><option :value="null">Create a new Supervisely team</option>
          <option v-for="t in catalog.remote_teams || []" :key="t.id" :value="t.id">Use existing: {{ t.name }} · {{ t.id }}</option></select></label>
        <datalist id="nj-user-logins"><option v-for="u in catalog.users || []" :key="u.id" :value="u.login">{{ u.name }}</option></datalist>
        <div class="nj-form-row"><label>First annotator login<input list="nj-user-logins" v-model="login1" autocomplete="off"></label>
          <label>Second annotator login<input list="nj-user-logins" v-model="login2" autocomplete="off"></label></div>
        <label class="nj-field">Assigned monitor<select v-model="monitorId"><option :value="null">Choose a monitor</option>
          <option v-for="u in monitors" :key="u.id" :value="u.id">{{ u.login }} · {{ u.id }}</option></select></label>
        <p class="nj-muted">Monitors must be Admins or Managers in the monitoring team. The selected monitor receives Manager permissions in the participant team.</p>
        <button class="nj-primary" @click="createTeam" :disabled="busy || blocked || !teamName.trim() || !login1.trim() || !login2.trim() || !monitorId">Register team and assign monitor</button>
      </div>
      <div class="nj-panel"><h3>2. Upload source data</h3>
        <p class="nj-muted">Upload files here, or use an existing dataset from the monitoring team in step 3. Keep images and videos in separate projects.</p>
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
    </div>
    <div v-if="roster.length" class="nj-panel"><h3>Team-to-monitor assignments</h3>
      <div class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Participant team</th><th>Annotators</th><th>Monitor</th><th></th></tr></thead>
        <tbody><tr v-for="t in roster" :key="t.id"><td>{{ t.name }} · {{ t.id }}</td><td>{{ t.annotator_ids.join(' / ') }}</td>
          <td><select :value="assignments[t.id] || t.monitor_id" @change="$set(assignments, t.id, Number($event.target.value))">
            <option v-for="u in monitors" :key="u.id" :value="u.id">{{ u.login }} · {{ u.id }}</option></select></td>
          <td><button @click="assign(t)" :disabled="busy || blocked">Save assignment</button></td></tr></tbody></table></div>
      <p class="nj-muted">Assignments control what each monitor can see in this app. Reassign before jobs are released. Existing Supervisely memberships remain unchanged.</p>
    </div>
    <div class="nj-panel"><h3>3. Preview data distribution</h3>
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
