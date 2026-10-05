/* All selection/filter/form state lives in this user's browser instance. */
Vue.component('nightjar-dashboard', {
  props: ['view', 'form', 'post'],
  data() {
    return { selectedTeam: null, selectedBatch: null, search: '', attentionOnly: false,
      entity: null, frame: 0, note: '', decision: 'accepted', busy: false, timer: null, tab: 'roster', setupBusy: false, clientError: '', showOriginal: false };
  },
  computed: {
    teams() { return this.view.snapshot.teams || []; },
    filteredTeams() { return this.teams.filter(t => t.name.toLowerCase().includes(this.search.toLowerCase())
      && (!this.attentionOnly || this.needsAttention(t))); },
    team() { return this.teams.find(t => t.id === this.selectedTeam) || this.filteredTeams[0] || null; },
    batch() { return this.team && (this.team.batches.find(b => b.id === this.selectedBatch)
      || this.team.batches.find(b => b.state === 'active' && !b.submitted || b.state === 'releasing')
      || this.team.batches.find(b => b.state === 'active' && b.submitted)
      || this.team.batches.find(b => b.state === 'locked') || this.team.batches.slice(-1)[0]); },
    workingBatches() { return (this.team?.batches || []).filter(b => (b.state === 'active' && !b.submitted) || b.state === 'releasing'); },
    submittedBatches() { return (this.team?.batches || []).filter(b => b.submitted || b.state === 'approved'); },
    upcomingBatches() { return (this.team?.batches || []).filter(b => b.state === 'locked'); },
    batchGroups() { return [{title: 'Current batches', items: this.workingBatches}, {title: 'Submitted batches', items: this.submittedBatches}, {title: 'Upcoming batches', items: this.upcomingBatches}]; },
    mediaPreview() { const p = this.view.review_preview; return p && p.team_id === this.team?.id && p.batch_id === this.batch?.id && p.entity_id === this.asset?.entity_id && p.frame_index === (this.batch?.kind === 'video' ? Number(this.frame) : -1) ? p : null; },
    savedReview() { return (this.batch?.reviews || []).find(r => r.entity_id === this.asset?.entity_id && r.frame_index === (this.batch?.kind === 'video' ? Number(this.frame) : -1)); },
    links() { return (this.view.links || []).filter(l => this.batch?.job_ids.includes(l.id)); },
    totals() { return { teams: this.teams.length, attention: this.teams.filter(this.needsAttention).length,
      approved: this.teams.reduce((n, t) => n + t.batches.filter(b => b.state === 'approved').length, 0) }; },
    asset() { return this.batch && (this.batch.assets.find(a => a.entity_id === Number(this.entity)) || this.batch.assets[0]); }
  },
  methods: {
    needsAttention(t) { return t.batches.some(b => b.open_flags || b.state === 'releasing'
      || (b.state === 'active' && (b.stale || b.sync_error || b.remote_status.includes('on_review')))); },
    time(epoch) { return epoch ? new Date(epoch * 1000).toLocaleTimeString() : 'Awaiting sync'; },
    date(value) { return value ? new Date(value).toLocaleString() : 'No actions in the activity window'; },
    chooseTeam(t) { this.selectedTeam = t.id; this.selectedBatch = null; this.entity = null; this.note = ''; this.frame = 0; this.showOriginal = false; },
    chooseBatch(b) { this.selectedBatch = b.id; this.entity = null; this.note = ''; this.frame = 0; },
    batchName(b) { return b.kind === 'video' ? 'Video batch' : 'Image batch ' + b.position; },
    batchStatus(b) { return b.state === 'approved' ? 'Approved' : b.submitted ? 'Submitted for review' : b.state === 'active' ? 'In progress' : b.state === 'locked' ? 'Not released' : 'Release needs reconciliation'; },
    participantName(uid) { const u = (this.view.directory?.annotators || []).find(u => u.id === uid); return u ? u.name + ' (' + u.login + ')' : 'Participant ' + uid; },
    async changeTab(tab) { if (this.busy || this.setupBusy) return; this.tab = tab; this.clientError = ''; if (tab === 'monitoring') await this.send('refresh'); },
    async reload() { if (this.tab === 'monitoring' || !this.view.connected) return this.send('refresh'); if (this.busy || this.setupBusy) return; this.busy = true; try { Object.assign(this.form, {setup_action:'overview'}); await this.post('/setup/action'); } catch (e) { this.clientError = 'Could not reload the event. Try again.'; } finally { this.busy = false; } },
    reviewAs(decision) { this.decision = decision; if (decision === 'accepted' && !this.note.trim()) this.note = this.batch.kind === 'video' ? 'Reviewed frame ' + Number(this.frame) + '.' : 'Reviewed image.'; return this.send('review'); },
    chooseReview(r) { this.entity = r.entity_id; this.frame = r.frame_index >= 0 ? r.frame_index : 0; this.note = r.note; },
    moveMedia(direction) { this.note = ''; if (this.batch.kind === 'video') this.frame = Math.min(Math.max(Number(this.frame) + direction, 0), this.asset.frames - 1); else { const i = this.batch.assets.findIndex(a => a.entity_id === this.asset.entity_id); this.entity = this.batch.assets[Math.min(Math.max(i + direction, 0), this.batch.assets.length - 1)].entity_id; } },
    async send(action) {
      if (this.busy || this.setupBusy) return;
      this.busy = true; this.clientError = '';
      Object.assign(this.form, { action, team_id: this.team?.id, batch_id: this.batch?.id,
        revision: this.team?.revision, entity_id: this.asset?.entity_id,
        frame_index: this.batch?.kind === 'video' ? Number(this.frame) : -1,
        note: this.note, decision: this.decision });
      try { await this.post('/dashboard/action'); }
      catch (e) { this.clientError = 'Request failed. Refresh before retrying an action.'; }
      finally { this.busy = false; }
    }
  },
  mounted() {
    this.send('refresh').then(() => { if (this.view.connected && !this.view.can_setup) this.tab = 'monitoring'; });
    this.timer = setInterval(() => { if (!document.hidden && !this.busy && this.tab === 'monitoring') this.send('refresh'); }, 60000);
  },
  beforeDestroy() { clearInterval(this.timer); },
  template: `
  <main class="nj-app">
    <header class="nj-header"><div><div class="nj-eyebrow">TÉCNICO · NIGHTJAR ANNOTATIATHON</div><h1>Annotation event</h1><p>Organize people, prepare data and review annotation progress.</p></div>
      <div class="nj-refresh"><span>Updated {{ time(view.snapshot.refreshed_at) }}</span><button @click="reload" :disabled="busy || setupBusy">{{ tab === 'monitoring' ? 'Refresh monitoring' : 'Refresh event' }}</button></div></header>
    <nightjar-connection v-if="view.connection && view.connection.local" :connection="view.connection" @connected="send('refresh').then(() => { if (view.can_setup) tab = 'roster'; })"></nightjar-connection>
    <nav class="nj-tabs" aria-label="Main app tabs" role="tablist">
      <button role="tab" :aria-selected="tab === 'roster'" :disabled="busy || setupBusy" :class="{selected: tab === 'roster'}" @click="changeTab('roster')">Monitors & Teams</button>
      <button role="tab" :aria-selected="tab === 'dataset'" :disabled="busy || setupBusy" :class="{selected: tab === 'dataset'}" @click="changeTab('dataset')">Dataset</button>
      <button role="tab" :aria-selected="tab === 'monitoring'" :disabled="busy || setupBusy" :class="{selected: tab === 'monitoring'}" @click="changeTab('monitoring')">Monitoring</button></nav>
    <div v-if="view.message" class="nj-message" :class="{'nj-error': view.error}" role="status">{{ view.message }}</div><div v-if="clientError" class="nj-message nj-error" role="alert">{{ clientError }}</div>
    <keep-alive><nightjar-management v-if="tab !== 'monitoring' && view.connected" :key="tab" :section="tab" :view="view" :form="form" :post="post" :locked="busy" @busy="setupBusy = $event"></nightjar-management></keep-alive>
    <section v-if="tab !== 'monitoring' && !view.connected" class="nj-empty"><h2>Connect to start your event</h2><p v-if="view.connection.local">Use Connect to Supervisely above. Then add monitors and teams in the first tab and prepare data in the second.</p><p v-else>Open this shared session through your Supervisely monitoring team.</p></section>
    <template v-if="tab === 'monitoring'">
      <div class="nj-section-heading"><div><h2>Monitoring</h2><p class="nj-muted">Select one of your assigned teams, inspect its current or submitted batches, and review images or video frames.</p></div></div>
      <section class="nj-metrics"><div><span>Your assigned teams</span><strong>{{ totals.teams }}</strong></div><div><span>Need attention</span><strong>{{ totals.attention }}</strong></div><div><span>Submitted for review</span><strong>{{ teams.reduce((n,t) => n + t.batches.filter(b => b.submitted && b.state !== 'approved').length,0) }}</strong></div><div><span>Approved batches</span><strong>{{ totals.approved }}</strong></div></section>
      <section v-if="!teams.length" class="nj-empty"><h3>No teams assigned to you</h3><p>The organiser can assign your teams in Monitors & Teams. Annotation review is available for your own assigned teams.</p></section>
      <section v-if="teams.length" class="nj-workspace"><aside class="nj-sidebar"><h3>Your teams</h3><input v-model="search" placeholder="Find a team" aria-label="Find a team"><label class="nj-check"><input type="checkbox" v-model="attentionOnly">Needs attention</label>
        <button v-for="t in filteredTeams" :key="t.id" class="nj-team" :class="{selected: team && team.id === t.id}" @click="chooseTeam(t)"><span>{{ t.name }}</span><small>{{ t.batches.filter(b => b.submitted && b.state !== 'approved').length }} submitted · {{ needsAttention(t) ? 'Needs attention' : 'On track' }}</small></button><p v-if="!filteredTeams.length">No teams match this filter.</p></aside>
        <div v-if="team && !batch" class="nj-panel"><h3>{{ team.name }}</h3><p>No batches prepared yet. Distribute its source data from Dataset.</p></div>
        <div v-if="team && batch" class="nj-detail"><div class="nj-team-heading"><div><h2>{{ team.name }}</h2><span>{{ team.annotator_ids.map(participantName).join(' · ') }}</span></div></div>
          <div class="nj-batch-groups"><section v-for="group in batchGroups" :key="group.title"><h3>{{ group.title }} <span class="nj-count">{{ group.items.length }}</span></h3><p v-if="!group.items.length" class="nj-muted">{{ group.title === 'Current batches' ? 'No batch in progress.' : group.title === 'Submitted batches' ? 'No batches submitted yet.' : 'No unreleased batches.' }}</p>
            <div class="nj-batches"><button v-for="b in group.items" :key="b.id" @click="chooseBatch(b)" :disabled="busy" :class="{selected: batch.id === b.id}">{{ batchName(b) }}<small>{{ batchStatus(b) }}</small></button></div></section></div>
          <div class="nj-panel"><div class="nj-panel-title"><h3>{{ batchName(batch) }}</h3><span class="nj-badge" :class="{warning: batch.open_flags || batch.stale}">{{ batchStatus(batch) }}</span></div>
            <p class="nj-muted">Supervisely status: {{ batch.remote_status }} · Last successful sync {{ time(batch.synced_at) }}</p>
            <div v-if="batch.job_ids.length" class="nj-progress"><template v-if="batch.kind === 'images'"><strong>{{ batch.completed == null ? '—' : batch.completed }} / {{ batch.total == null ? '—' : batch.total }}</strong> confirmed images<progress :value="batch.completed || 0" :max="batch.total || 1"></progress></template><template v-else><strong>{{ batch.assets[0].frames.toLocaleString() }}</strong> frames in the complete video<p class="nj-muted">Submission is reported for the whole video. Individual frame completion is not inferred.</p></template></div>
            <p v-if="batch.stale && batch.job_ids.length" class="nj-caution">Progress is stale. Refresh Supervisely before approving this batch.</p><p v-if="batch.sync_error" class="nj-caution">{{ batch.sync_error }}</p>
            <div class="nj-actions"><button v-if="batch.job_ids.length" @click="send('sync')" :disabled="busy">Refresh Supervisely progress</button><button v-if="batch.job_ids.length" @click="send('open')" :disabled="busy">Open labeling jobs</button><button v-if="batch.state === 'locked'" class="nj-primary" @click="send('release')" :disabled="busy">Release this batch</button><button v-if="batch.state === 'releasing'" class="nj-primary" @click="send('reconcile')" :disabled="busy">Reconcile release</button></div>
            <div class="nj-links"><a v-for="link in links" :key="link.id" :href="link.url" target="_blank" rel="noopener noreferrer">Open job {{ link.id }} ↗</a></div></div>
          <div v-if="batch.state !== 'locked' && batch.state !== 'releasing'" class="nj-panel"><h3>Inspect and mark annotations</h3>
            <div class="nj-form-row"><label>Image / video<select v-model="entity" @change="note = ''"><option :value="null">{{ batch.assets[0].name || batch.assets[0].source_id }}</option><option v-for="a in batch.assets" :key="a.entity_id" :value="a.entity_id">{{ a.name || a.source_id }}</option></select></label>
              <label v-if="batch.kind === 'video'">Frame (starts at 0)<input v-model.number="frame" type="number" min="0" :max="asset.frames - 1" @change="note = ''"></label></div>
            <div class="nj-actions"><button @click="moveMedia(-1)" :disabled="busy">Previous {{ batch.kind === 'video' ? 'frame' : 'image' }}</button><button class="nj-primary" @click="send('preview_media')" :disabled="busy || (batch.kind === 'video' && (!Number.isInteger(Number(frame)) || frame < 0 || frame >= asset.frames))">Load image / frame</button><button @click="moveMedia(1)" :disabled="busy">Next {{ batch.kind === 'video' ? 'frame' : 'image' }}</button></div>
            <template v-if="mediaPreview"><div class="nj-section-heading nj-preview-heading"><span>{{ mediaPreview.labels }} annotation objects · Loaded {{ time(mediaPreview.loaded_at) }}</span><label class="nj-check"><input type="checkbox" v-model="showOriginal">Show original without annotations</label></div><div class="nj-legend"><span v-for="c in mediaPreview.classes || []" :key="c.name"><i :style="{background: 'rgb(' + c.color.join(',') + ')'}"></i>{{ c.name }}</span></div><img class="nj-media-preview" :src="showOriginal ? mediaPreview.original : mediaPreview.annotated" :alt="showOriginal ? 'Original image or frame' : 'Current saved annotation overlay'"></template>
            <p v-else class="nj-muted">Load the selected image or frame to see the saved annotation overlay, or inspect the labeling job in Supervisely.</p>
            <p v-if="savedReview" class="nj-message">Saved decision: {{ savedReview.decision === 'accepted' ? 'Well annotated' : 'Needs correction' }} — {{ savedReview.note }}</p>
            <label class="nj-note">Review note<textarea v-model="note" maxlength="4000" rows="2" placeholder="Describe any correction needed, or add an optional note for an accepted annotation."></textarea></label>
            <div class="nj-actions"><button class="nj-success" @click="reviewAs('accepted')" :disabled="busy">Mark well annotated</button><button class="nj-danger" @click="reviewAs('correction')" :disabled="busy || !note.trim()">Mark needs correction</button><button v-if="batch.kind === 'images' && savedReview" @click="send('publish')" :disabled="busy">Publish saved image decision to Supervisely</button></div>
            <p class="nj-muted">Image and frame decisions are recorded here. Frame flags do not accept or reject a whole video. Detailed correction notes are internal monitoring records.</p>
            <div v-if="batch.state === 'active' && batch.submitted" class="nj-approval"><h4>Approve the submitted batch</h4><p class="nj-muted">Inspect a sample and resolve all correction flags first. Approval unlocks the next batch for release.</p><button class="nj-primary" @click="send('approve')" :disabled="busy || !note.trim()">Approve reviewed batch</button></div></div>
          <div class="nj-columns"><div class="nj-panel"><h3>Participant activity</h3><p class="nj-muted">Labeling actions in the last {{ batch.activity.window_minutes || 15 }} minutes.</p><div v-for="uid in team.annotator_ids" :key="uid" class="nj-contribution"><span>{{ participantName(uid) }}</span><strong>{{ batch.activity.actions_by_user ? (batch.activity.actions_by_user[uid] || 0) : '—' }}</strong></div><p class="nj-muted">{{ date(batch.activity.last_action_at) }} · Updated {{ time(batch.activity_at) }}</p></div>
            <div class="nj-panel"><h3>Review summary</h3><div class="nj-contribution"><span>Unresolved corrections</span><strong>{{ batch.open_flags }}</strong></div><div class="nj-contribution"><span>Reviewed images / frames</span><strong>{{ batch.reviews.length }}</strong></div></div></div>
          <div v-if="batch.reviews.length" class="nj-panel"><h3>Saved review records</h3><div v-for="r in batch.reviews" :key="r.entity_id + ':' + r.frame_index" class="nj-review"><button @click="chooseReview(r)" :disabled="busy">Select {{ r.frame_index >= 0 ? 'frame ' + r.frame_index : 'image ' + r.entity_id }}</button><span class="nj-badge" :class="{warning: r.decision === 'correction'}">{{ r.decision === 'accepted' ? 'Well annotated' : 'Needs correction' }}</span><p>{{ r.note }}</p></div></div>
        </div></section>
    </template>
  </main>`
});
