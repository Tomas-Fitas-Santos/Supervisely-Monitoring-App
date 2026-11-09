/* Settings for the existing native event team/workspace and verified IDs. */
Vue.component('event-settings', {
  props: ['view','form','post','locked'],
  data() {return {teamId:'',workspaceId:'',monitorId:'',demoDualRole:false,
    busy:false,error:'',initialized:false};},
  computed: {
    settings() {return this.view.event_settings||{};},
    isAdmin() {return !!this.view.can_setup;},
    connected() {return !!this.view.connected;},
    nativeReady() {return !!this.settings.single_team;}
  },
  watch: {
    settings:{immediate:true,handler(s){
      if(!s)return;
      if(!this.initialized || (!this.busy && !this.error)){
        this.teamId=s.team_id?s.team_id.toString():'';
        this.workspaceId=s.workspace_id?s.workspace_id.toString():'';
        this.initialized=true;
      }
    }}
  },
  methods: {
    async send(action, values={}) {
      if(this.busy||this.locked)return;
      this.busy=true;this.error='';
      Object.assign(this.form,{settings_action:action},values);
      try {
        const response=await this.post('/settings/action');
        if(response?.ok===false||this.view.error)
          throw new Error(this.view.message||'Settings update was not accepted.');
        if(action==='add_monitor')this.monitorId='';
        return true;
      } catch(e){this.error=e.message||'Settings could not be saved.';return false;}
      finally{this.busy=false;}
    },
    async save() {
      return this.send('save_binding',{team_id:this.teamId,workspace_id:this.workspaceId,
        settings_revision:this.settings.revision});
    },
    async addMonitor() {
      return this.send('add_monitor',{monitor_user_id:this.monitorId,demo_dual_role:this.demoDualRole});
    }
  },
  template:`<section class="nj-management nj-event-settings">
    <div class="nj-section-heading"><div><h2>Application settings</h2>
      <p class="nj-muted">Both DEMO and PRODUCTION connect to a team and workspace
        already created in Supervisely. The app never creates the native team.</p></div>
      <button @click="send('refresh')" :disabled="busy||locked">Refresh settings</button>
    </div>
    <p v-if="error" role="alert" class="nj-message nj-error">{{error}}</p>
    <div class="nj-panel">
      <h3>Active Supervisely identity</h3>
      <div class="nj-columns">
        <div><strong>User ID</strong><p>{{settings.user_id||'Not connected'}}</p></div>
        <div><strong>Account</strong><p>{{view.connection?.login||'Supervisely session'}}</p></div>
      </div>
      <p class="nj-muted">The User ID is checked against your authenticated Supervisely
        account. An ID alone never logs you in or grants monitoring access.
        On a locally hosted single-user instance you can switch accounts using
        the personal API token form above. For simultaneous independent monitors,
        run the app with real multi-user authentication on Supervisely.</p>
    </div>
    <div class="nj-panel">
      <h3>Existing event resources</h3>
      <p class="nj-muted">Enter the IDs of the one native Supervisely team and the
        pre-existing workspace where this event's source dataset will be uploaded.</p>
      <div class="nj-form-row">
        <label>Supervisely Team ID
          <input v-model="teamId" type="number" min="1" step="1" :disabled="busy||locked||!isAdmin"
            placeholder="Existing team ID" autocomplete="off">
        </label>
        <label>Source Workspace ID
          <input v-model="workspaceId" type="number" min="1" step="1" :disabled="busy||locked||!isAdmin"
            placeholder="Existing workspace ID" autocomplete="off">
        </label>
      </div>
      <p v-if="nativeReady">Bound to Team {{settings.team_id}} · Workspace {{settings.workspace_id}}</p>
      <p v-else class="nj-caution">Configure the shared team and workspace before sending invitations or uploading data.</p>
      <button v-if="isAdmin" class="nj-primary" @click="save"
        :disabled="busy||locked||!teamId||!workspaceId">Verify and save IDs</button>
      <p class="nj-muted">Only the event organiser may change these IDs. Changing
        the team is blocked after roster or invitation data exists; changing
        the workspace is blocked after dataset import or distribution.</p>
    </div>
    <div class="nj-panel">
      <h3>CSV-declared monitoring staff</h3>
      <p class="nj-muted">Import roles in Monitors &amp; Teams first. The app invites each
        CSV-declared monitor with the corresponding native role and registers
        monitoring access after acceptance. The control below can reconcile
        an already accepted CSV-declared monitor by their real User ID;
        it does not elevate someone who is missing from the roster.</p>
      <div class="nj-form-row">
        <label>Monitor User ID
          <input v-model="monitorId" type="number" min="1" step="1"
            :disabled="busy||locked||!isAdmin" autocomplete="off"></label>
      </div>

      <button v-if="isAdmin" @click="addMonitor"
        :disabled="busy||locked||!nativeReady||!monitorId">Reconcile registered monitor</button>
    </div>
    <p class="nj-muted">Theme is selected in the main header and is stored locally
      in this browser. It does not change event records or account permissions.</p>
  </section>`
});
