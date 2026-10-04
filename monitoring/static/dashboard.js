/* All selection/filter/form state lives in this user's browser instance. */
Vue.component('nightjar-dashboard', {
  props: ['view', 'form', 'post'],
  data() {
    return { selectedTeam: null, selectedBatch: null, search: '', attentionOnly: false,
      entity: null, frame: 0, note: '', decision: 'accepted', busy: false, timer: null };
  },
  computed: {
    teams() { return this.view.snapshot.teams || []; },
    filteredTeams() { return this.teams.filter(t => t.name.toLowerCase().includes(this.search.toLowerCase())
      && (!this.attentionOnly || this.needsAttention(t))); },
    team() { return this.teams.find(t => t.id === this.selectedTeam) || this.filteredTeams[0] || null; },
    batch() { return this.team && (this.team.batches.find(b => b.id === this.selectedBatch)
      || this.team.batches.find(b => b.state === 'active' || b.state === 'releasing')
      || this.team.batches.find(b => b.state === 'locked') || this.team.batches.slice(-1)[0]); },
    totals() { return { teams: this.teams.length, attention: this.teams.filter(this.needsAttention).length,
      approved: this.teams.reduce((n, t) => n + t.batches.filter(b => b.state === 'approved').length, 0) }; },
    asset() { return this.batch && (this.batch.assets.find(a => a.entity_id === Number(this.entity)) || this.batch.assets[0]); }
  },
  methods: {
    needsAttention(t) { return t.batches.some(b => b.open_flags || b.state === 'releasing'
      || (b.state === 'active' && (b.stale || b.sync_error || b.remote_status.includes('on_review')))); },
    time(epoch) { return epoch ? new Date(epoch * 1000).toLocaleTimeString() : 'Awaiting sync'; },
    date(value) { return value ? new Date(value).toLocaleString() : 'No actions in the activity window'; },
    chooseTeam(t) { this.selectedTeam = t.id; this.selectedBatch = null; this.entity = null; this.note = ''; },
    chooseBatch(b) { this.selectedBatch = b.id; this.entity = null; this.note = ''; this.frame = 0; },
    async send(action) {
      if (this.busy) return;
      this.busy = true;
      Object.assign(this.form, { action, team_id: this.team?.id, batch_id: this.batch?.id,
        revision: this.team?.revision, entity_id: this.asset?.entity_id,
        frame_index: this.batch?.kind === 'video' ? Number(this.frame) : -1,
        note: this.note, decision: this.decision });
      try { await this.post('/dashboard/action'); }
      finally { this.busy = false; }
    }
  },
  mounted() {
    this.send('refresh');
    this.timer = setInterval(() => { if (!document.hidden && !this.busy) this.send('refresh'); }, 60000);
  },
  beforeDestroy() { clearInterval(this.timer); },
  template: `
  <main class="nj-app">
    <header class="nj-header">
      <div><div class="nj-eyebrow">TÉCNICO · NIGHTJAR ANNOTATIATHON</div>
      <h1>Annotation monitor</h1><p>Your teams, their progress, and the next decision.</p></div>
      <div class="nj-refresh"><span>Updated {{ time(view.snapshot.refreshed_at) }}</span>
      <button @click="send('refresh')" :disabled="busy">Refresh dashboard</button></div>
    </header>
    <section class="nj-metrics">
      <div><span>Assigned teams</span><strong>{{ totals.teams }}</strong></div>
      <div><span>Need attention</span><strong>{{ totals.attention }}</strong></div>
      <div><span>Approved batches</span><strong>{{ totals.approved }}</strong></div>
      <div><span>Refresh interval</span><strong>60<span class="nj-unit">sec</span></strong></div>
    </section>
    <div v-if="view.message" class="nj-message" :class="{ 'nj-error': view.error }" role="status">{{ view.message }}</div>
    <section v-if="!teams.length" class="nj-empty"><h2>No teams assigned</h2>
      <p>Your organiser needs to import the event manifest and assign your Supervisely user ID to participant teams.</p></section>
    <section v-else class="nj-workspace">
      <aside class="nj-sidebar"><h2>Your teams</h2><input v-model="search" placeholder="Find a team" aria-label="Find a team">
        <label class="nj-check"><input type="checkbox" v-model="attentionOnly"> Needs attention</label>
        <button v-for="t in filteredTeams" :key="t.id" class="nj-team" :class="{selected: team && team.id === t.id}"
          @click="chooseTeam(t)"><span>{{ t.name }}</span><small>{{ needsAttention(t) ? 'Needs attention' : 'On track' }}</small></button>
        <p v-if="!filteredTeams.length">No teams match these filters.</p>
      </aside>
      <div v-if="team && batch" class="nj-detail">
        <div class="nj-team-heading"><div><h2>{{ team.name }}</h2><span>Participant IDs {{ team.annotator_ids.join(' · ') }}</span></div>
        <span class="nj-badge">{{ batch.kind === 'video' ? 'Final video' : 'Image batches' }}</span></div>
        <nav class="nj-batches" aria-label="Batch history">
          <button v-for="b in team.batches" :key="b.id" @click="chooseBatch(b)" :class="{selected: batch.id === b.id}">
            {{ b.kind === 'video' ? 'Final video' : 'Batch ' + b.position }}<small>{{ b.state }}</small></button>
        </nav>
        <div class="nj-panel"><div class="nj-panel-title"><h3>{{ batch.kind === 'video' ? 'Complete video task' : 'Batch ' + batch.position }}</h3>
          <span class="nj-badge" :class="{warning: batch.open_flags || batch.stale}">{{ batch.state }}</span></div>
          <p class="nj-muted">{{ batch.remote_status }} · Last successful sync {{ time(batch.synced_at) }}</p>
          <div v-if="batch.job_ids.length" class="nj-progress">
            <template v-if="batch.kind === 'images'"><strong>{{ batch.completed == null ? '—' : batch.completed }} / {{ batch.total == null ? '—' : batch.total }}</strong> confirmed images
              <progress :value="batch.completed || 0" :max="batch.total || 1"></progress>
              <span v-if="batch.total != null">{{ batch.total - (batch.completed || 0) }} remaining</span></template>
            <template v-else><strong>{{ batch.assets[0].frames.toLocaleString() }}</strong> frames in the complete source video
              <p>Progress uses submission of the complete video job. Frame completion is not inferred.</p></template>
          </div>
          <p v-if="batch.stale && batch.job_ids.length" class="nj-caution">Progress is stale. Refresh Supervisely before approving.</p>
          <p v-if="batch.sync_error" class="nj-caution">{{ batch.sync_error }}</p>
          <div class="nj-actions">
            <button v-if="batch.job_ids.length" @click="send('sync')" :disabled="busy">Refresh Supervisely</button>
            <button v-if="batch.job_ids.length" @click="send('open')" :disabled="busy">Inspect in Supervisely</button>
            <button v-if="batch.state === 'locked'" class="nj-primary" @click="send('release')" :disabled="busy">Release task</button>
            <button v-if="batch.state === 'releasing'" class="nj-primary" @click="send('reconcile')" :disabled="busy">Reconcile release</button>
          </div>
          <div class="nj-links"><a v-for="link in view.links" :key="link.id" :href="link.url" target="_blank" rel="noopener noreferrer">Open job {{ link.id }} ↗</a></div>
        </div>
        <div class="nj-columns">
          <div class="nj-panel"><h3>Participant activity</h3><p class="nj-muted">Labeling actions in the last {{ batch.activity.window_minutes || 15 }} minutes.</p>
            <div v-for="uid in team.annotator_ids" :key="uid" class="nj-contribution"><span>Participant {{ uid }}</span>
              <strong>{{ batch.activity.actions_by_user ? (batch.activity.actions_by_user[uid] || 0) : '—' }}</strong></div>
            <p class="nj-muted">{{ date(batch.activity.last_action_at) }}</p><p class="nj-muted">Activity updated {{ time(batch.activity_at) }}. Action counts describe activity, not quality.</p></div>
          <div class="nj-panel"><h3>Review summary</h3><div class="nj-contribution"><span>Unresolved correction flags</span><strong>{{ batch.open_flags }}</strong></div>
            <div class="nj-contribution"><span>Inspected images / frames</span><strong>{{ batch.reviews.length }}</strong></div>
            <p class="nj-muted">Current and submitted batches remain available for inspection.</p></div>
        </div>
        <div v-if="batch.state !== 'locked' && batch.state !== 'releasing'" class="nj-panel">
          <h3>Record a review</h3><p class="nj-muted">Inspect the annotation in Supervisely, then record the decision here. Video frame numbers start at zero.</p>
          <div class="nj-form-row"><label>Image / video<select v-model="entity"><option :value="null">{{ batch.assets[0].source_id }}</option>
            <option v-for="a in batch.assets" :key="a.entity_id" :value="a.entity_id">{{ a.source_id }} · {{ a.entity_id }}</option></select></label>
            <label v-if="batch.kind === 'video'">Frame<input v-model.number="frame" type="number" min="0" :max="batch.assets[0].frames - 1"></label>
            <label>Decision<select v-model="decision"><option value="accepted">Correctly annotated</option><option value="correction">Needs correction</option></select></label></div>
          <label class="nj-note">Review note<textarea v-model="note" maxlength="4000" rows="3" placeholder="What did you inspect? What needs correcting?"></textarea></label>
          <div class="nj-actions"><button class="nj-primary" @click="send('review')" :disabled="busy || !note.trim()">Save review</button>
            <button v-if="batch.kind === 'images'" @click="send('publish')" :disabled="busy">Publish saved image decision</button>
            <button v-if="batch.state === 'active'" @click="send('approve')" :disabled="busy || !note.trim()">Approve reviewed batch</button></div>
          <p class="nj-muted">Detailed correction notes and video frame flags stay in the monitoring app. Participant alerts and correction-job creation are planned next.</p>
        </div>
        <div class="nj-panel" v-if="batch.reviews.length"><h3>Review records</h3><div class="nj-review" v-for="r in batch.reviews" :key="r.entity_id + ':' + r.frame_index">
          <span class="nj-badge" :class="{warning: r.decision === 'correction'}">{{ r.decision }}</span>
          <strong>Entity {{ r.entity_id }}<template v-if="r.frame_index >= 0"> · frame {{ r.frame_index }}</template></strong><p>{{ r.note }}</p>
        </div></div>
      </div>
    </section>
  </main>`
});
