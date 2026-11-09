/* Single event roster: Excel source preservation, role adjustments, pair staging.
 * Never send invitations as a side effect of file import or role editing.
 */
Vue.component('event-registrations', {
  props:['view','run','form','post','disabled'],
  data() {
    return {file:null,busy:false,error:'',search:'',group:'Monitors',
      roleEdits:{},demoEmails:[],showFields:false};
  },
  computed: {
    profiles(){return this.view.roster_profiles||[];},
    monitors(){return this.profiles.filter(p=>p.source_sheet==='Monitors');},
    participants(){return this.profiles.filter(p=>p.source_sheet==='Participants');},
    selected(){
      const q=this.search.trim().toLowerCase();
      return this.profiles.filter(p=>p.source_sheet===this.group&&
        (!q||[p.email,p.display_name,p.team_name,p.team_number].some(
          v=>String(v||'').toLowerCase().includes(q))));
    },
    eligibleDemo(){
      return this.monitors.filter(p=>
        (this.roleEdits[p.id]||p.event_role)==='participant_monitor'&&!!p.email);
    },
    reviewCount(){return this.participants.filter(p=>p.needs_review).length;},
    completeTeams(){
      const teams={};
      for(const p of this.participants) {
        if(p.needs_review||!p.team_name||!p.email)continue;
        (teams[p.team_name]||(teams[p.team_name]=[])).push(p);
      }
      return Object.values(teams).filter(v=>v.length===2&&
        v[0].email!==v[1].email&&v.every(p=>p.event_role!=='monitor')).length;
    }
  },
  methods:{
    fileChanged(e){this.file=e.target.files?.[0]||null;this.error='';},
    async load() {
      if(this.disabled||this.busy||!this.file)return;
      if(!/\.xlsx$/i.test(this.file.name)||this.file.size>3*1024*1024){
        this.error='Choose a two-tab Monitors / Participants .xlsx below 3 MiB.';return;
      }
      this.busy=true;this.error='';
      try {
        const base64=await new Promise((resolve,reject)=>{
          const reader=new FileReader();
          reader.onload=()=>resolve(String(reader.result).split(',')[1]);
          reader.onerror=()=>reject(new Error('Could not read the roster workbook.'));
          reader.readAsDataURL(this.file);
        });
        if(!await this.run('roster_workbook_import',{xlsx_base64:base64}))
          throw new Error(this.view.message||'Workbook import failed.');
        this.demoEmails=[];this.roleEdits={};
      }catch(e){this.error=e.message||'Could not import workbook.';}
      finally{this.busy=false;}
    },
    async role(person) {
      const choice=this.roleEdits[person.id]||person.event_role;
      if(choice===person.event_role)return;
      if(!await this.run('roster_role',{record_id:person.id,event_role:choice})){
        this.error=this.view.message||'Could not change role.';
      }else this.$delete(this.roleEdits,person.id);
    },
    async demoPair(){
      if(this.demoEmails.length!==2)return;
      if(!await this.run('roster_demo_pair',{emails:this.demoEmails}))
        this.error=this.view.message||'Could not create rehearsal pair.';
    },
    async command(action) {
      if(!await this.run(action))this.error=this.view.message||'Action failed.';
      else this.error='';
    },
    asPairs(){return this.command('roster_prepare_pairs');},
    invite(){return this.command('registration_provision_all');},
    refresh(){return this.command('registration_refresh');}
  },
  template:`<section class="nj-panel nj-people-roster">
    <h3>Official registration roster &amp; roles</h3>
    <p class="nj-muted">Load one workbook with two sheets: Monitors and Participants.
      The application stores every original column, even when a record cannot be
      invited. Importing the workbook does not invite anyone.</p>
    <div class="nj-form-row">
      <label>Event roster (.xlsx)
        <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          @change="fileChanged" :disabled="disabled||busy">
      </label>
      <button class="nj-primary" @click="load" :disabled="disabled||busy||!file">Import both sheets</button>
    </div>
    <p v-if="error" class="nj-message nj-error" role="alert">{{error}}</p>
    <p v-if="view.roster_import_result" class="nj-message">
      {{view.roster_import_result.monitors}} monitors,
      {{view.roster_import_result.participants}} participants;
      {{view.roster_import_result.needs_review}} records need review.
      No invitations were sent.
    </p>
    <div class="nj-metrics nj-three-metrics">
      <div><span>Monitor records</span><strong>{{monitors.length}}</strong></div>
      <div><span>Participant records</span><strong>{{participants.length}}</strong></div>
      <div><span>Needs review</span><strong>{{reviewCount}}</strong></div>
    </div>
    <div v-if="monitors.length" class="nj-panel">
      <h4>DEMO trial — use two real monitor accounts for annotation</h4>
      <p class="nj-muted">Choose two monitors, set each event role to
        Participant + Monitor and save. The app will create one logical
        rehearsal pair; other staff remain monitors. No simulated emails.</p>
      <div class="nj-check-group">
        <label v-for="m in eligibleDemo" :key="m.id" class="nj-check">
          <input type="checkbox" :value="m.email" v-model="demoEmails"
            :disabled="disabled||busy||(!demoEmails.includes(m.email)&&demoEmails.length>=2)">
          {{m.display_name||m.email}}
        </label>
      </div>
      <button @click="demoPair" :disabled="disabled||busy||demoEmails.length!==2">
        Create two-person DEMO pair
      </button>
    </div>
    <div v-if="participants.length" class="nj-panel">
      <h4>PRODUCTION — prepare eligible pairs</h4>
      <p class="nj-muted">Eligible complete pairs: {{completeTeams}}.
        Incomplete teams, cancelled registrations, missing emails, records with
        missing processing consent or conflicting roles are retained for review.
        Preparing pairs never sends an invitation.</p>
      <button @click="asPairs" :disabled="disabled||busy||!completeTeams">
        Prepare complete eligible pairs
      </button>
    </div>
    <div class="nj-actions">
      <button @click="refresh" :disabled="disabled||busy">Refresh Supervisely membership statuses</button>
      <button class="nj-primary" @click="invite"
        :disabled="disabled||busy||!monitors.length">
        Send role-aware invitations to prepared event users
      </button>
    </div>
    <p class="nj-caution">Sending invitations acts on real Supervisely
      accounts. Review the declared roles and the prepared teams before clicking.
      Existing accepted native roles cannot be silently changed by CSV reimport.</p>
    <div class="nj-form-row">
      <label>Sheet<select v-model="group">
        <option value="Monitors">Monitors</option>
        <option value="Participants">Participants</option></select></label>
      <label>Find name, email or team<input v-model="search" placeholder="Filter records"></label>
    </div>
    <div class="nj-table-scroll"><table class="nj-table">
      <thead><tr><th>Team</th><th>Name / email</th><th>Event role</th>
        <th>Invitation</th><th>Review</th><th>Fields</th></tr></thead>
      <tbody><tr v-for="p in selected" :key="p.id">
        <td>{{p.team_name||'Staff'}}</td>
        <td><strong>{{p.display_name||'—'}}</strong>
          <small class="nj-small-text">{{p.email||'Email missing'}}</small></td>
        <td><div class="nj-inline-actions">
          <select :value="roleEdits[p.id]||p.event_role"
            @change="$set(roleEdits,p.id,$event.target.value)" :disabled="disabled||busy">
            <option value="monitor">Monitor</option>
            <option value="participant">Participant</option>
            <option value="participant_monitor">Participant + Monitor</option>
          </select>
          <button @click="role(p)" :disabled="disabled||busy||
            !roleEdits[p.id]||roleEdits[p.id]===p.event_role">Save</button>
        </div></td>
        <td>{{p.invitation_status}}</td>
        <td><span v-if="p.needs_review" class="nj-badge warning">Review</span>
          <span v-else>Ready to evaluate</span></td>
        <td><details><summary>Source columns</summary>
          <div v-for="(value,key) in p.source_fields" :key="key">
            <strong>{{key}}</strong>: {{value||'—'}}</div>
        </details></td>
      </tr></tbody>
    </table></div>
    <p v-if="!selected.length" class="nj-muted">No matching records in this sheet.</p>
    <p class="nj-muted">Only event organisers can see the full registration
      fields, including private personal information. Check-in and reception
      surfaces show the minimum information needed by monitors.</p>
  </section>`
});
