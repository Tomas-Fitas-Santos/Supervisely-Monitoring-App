/* Shared roster and dataset steps, using the authenticated request bridge. */
Vue.component('nightjar-management', {
  props:['view','form','post','section','locked'],
  data(){return {busy:false,loading:false,clientError:'',inspectionNote:'',showUpload:false,loaded:false,assignmentOpen:false};},
  computed:{
    setup(){return this.view.setup||{};},
    directory(){return this.view.directory||{};},
    sources(){return this.view.sources||{datasets:[],workspaces:[]};},
    blocked(){return !!this.setup.blocked_by;},
    disabled(){return this.busy||this.locked||this.blocked;}
  },
  watch:{
    busy(value){this.$emit('busy',value);},
    locked(value){if(!value&&!this.loaded&&this.section==='dataset'&&this.view.connected)this.reload();}
  },
  methods:{
    async run(action,values={}){
      const reading=['overview','sources'].includes(action);
      if(this.busy||this.locked||(reading&&this.loading))return false;
      if(reading)this.loading=true;else this.busy=true;this.clientError='';Object.assign(this.form,values,{setup_action:action});
      try{
        const result=await this.post('/setup/action');
        if(result?.ok===false||this.view.error)throw new Error(this.view.message||'Could not update this setup step.');
        return true;
      }catch(error){this.clientError=error.message;return false;}
      finally{if(reading)this.loading=false;else this.busy=false;}
    },
    async reload(){if(!this.view.connected)return;const ok=await this.run(this.section==='dataset'?'sources':'overview');if(ok)this.loaded=true;}
  },
  mounted(){if(this.section==='dataset'&&!this.locked&&this.view.connected)this.reload();},
  template: `
  <section class="nj-management">
    <div class="nj-section-heading"><div><h2>{{ section === 'roster' ? 'People & invitations' : 'Dataset' }}</h2>
      <p class="nj-muted">{{ section === 'roster' ? 'Upload your roster, check the people lists and choose who to invite.' : 'Upload event source data, choose teams and review distribution before applying it.' }}</p></div>
      <button @click="reload" :disabled="busy || locked || loading">{{loading?'Loading…':'Reload lists'}}</button></div>
    <p v-if="!view.connected" class="nj-message">First connect your organiser account using the Supervisely server and API token above. Event setup becomes available after connection.</p>
    <p v-else-if="!view.can_setup" class="nj-message">You can view the event lists. The monitoring organiser manages people, assignments and dataset distribution.</p>
    <div v-if="clientError" class="nj-message nj-error" role="alert">{{ clientError }}</div>
    <div v-if="blocked && view.can_setup" class="nj-panel nj-caution"><h3>Setup needs attention</h3><p>Check the operation history at the bottom of this tab before continuing.</p>
      <template v-if="(setup.operations || []).some(o => o.id === setup.blocked_by && o.state === 'needs_inspection')">
        <label>What did you verify in Supervisely?<textarea v-model="inspectionNote" rows="2" maxlength="2000"></textarea></label>
        <button @click="run('inspect', {operation_id: setup.blocked_by, inspection_note: inspectionNote})" :disabled="busy || locked || !inspectionNote.trim()">Record inspection and continue</button>
      </template></div>
    <div v-if="!directory.ready" class="nj-panel"><h3>Connect the existing event team</h3>
      <p>Complete “1. Connect event” above with your Supervisely Team ID and Workspace ID, then return here.</p></div>
    <template v-if="section === 'roster'"><event-registrations v-if="view.can_setup && directory.ready" :view="view" :run="run" :form="form" :post="post" :disabled="disabled" @busy="$emit('busy',$event)"></event-registrations>
      <details v-if="view.can_setup && directory.ready && (view.registration_rows||[]).length" class="nj-panel" @toggle="assignmentOpen=$event.target.open"><summary>Optional: adjust team assignments</summary><event-roster-assignment v-if="assignmentOpen" :view="view" :run="run" :disabled="disabled"></event-roster-assignment></details>
    </template>
    <template v-if="section === 'dataset' && directory.ready">
      <div class="nj-panel"><h3>1. Choose your source data</h3><p class="nj-muted">Use a dataset already in Supervisely, or upload a new folder or ZIP. Select the datasets for your teams in the next section.</p>
        <div v-if="(sources.datasets||[]).length" class="nj-table-scroll"><table class="nj-table"><thead><tr><th>Project / dataset</th><th>Type</th><th>Files</th></tr></thead><tbody><tr v-for="d in sources.datasets" :key="d.id"><td>{{ d.project }} / {{ d.name }}</td><td>{{ d.kind === 'images' ? 'Images' : 'Videos' }}</td><td>{{ d.items }}</td></tr></tbody></table></div><p v-if="!(sources.datasets || []).length">No source data yet. Upload files below, or import a dataset into the monitoring team in Supervisely and reload this list.</p></div>
      <button v-if="(sources.datasets||[]).length" @click="showUpload=!showUpload" :disabled="disabled">{{showUpload?'Use existing datasets':'Upload another dataset'}}</button>
      <event-structured-upload v-if="view.can_setup && view.event_settings?.single_team && (showUpload || !(sources.datasets||[]).length)"
        :view="view" :form="form" :post="post" :locked="disabled" @busy="$emit('busy',$event)" @uploaded="showUpload=false;$nextTick(reload)"></event-structured-upload>
      <p v-else-if="view.can_setup && !view.event_settings?.single_team" class="nj-caution">Complete “1. Connect event” before importing a dataset.</p>
      <event-job-planner v-if="view.can_setup && view.event_settings?.single_team && (sources.datasets||[]).length"
        :view="view" :run="run" :disabled="disabled"></event-job-planner>
      <p v-else-if="view.can_setup && !view.event_settings?.single_team" class="nj-muted">Connect the event before preparing jobs.</p>
    </template>
    <details v-if="view.can_setup && (setup.operations || []).length" class="nj-panel"><summary>Operation history and recovery</summary><div v-for="o in setup.operations" :key="o.id" class="nj-operation"><strong>{{ o.kind }} · {{ o.state }}</strong><small>{{ o.id }}</small><p v-if="o.result.error" class="nj-caution">{{ o.result.error }}</p><details v-if="o.result.resources"><summary>Confirmed resources</summary><pre>{{ JSON.stringify(o.result.resources, null, 2) }}</pre></details></div></details>
  </section>`
});
