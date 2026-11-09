/* Single event roster: Excel source preservation, role adjustments, pair staging.
 * Never send invitations as a side effect of file import or role editing.
 */
Vue.component('event-registrations', {
  props:['view','run','form','post','disabled'],
  data() {
    return {file:null,busy:false,error:'',search:'',group:'Monitors',
      roleEdits:{},loginEdits:{},nativeEmailEdits:{},pairName:'',pairEmails:[],pairEligible:false,
      inviteEmails:[],inviteFailures:[],lastSync:null,
      invitationAuthBlocked:false,showFields:false};
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
    nativeProfiles(){return this.profiles.filter(p=>p.source_fields?.imported_via==='supervisely');},
    acceptedMembers(){return this.profiles.filter(p=>p.invitation_status==='accepted').length;},
    unpaired(){
      const seen=new Set();
      return this.profiles.filter(p=>{
        if(!p.can_pair||seen.has(p.email))return false;
        seen.add(p.email);return true;
      });
    },
    invitableEmails(){
      return new Set(this.profiles.filter(p=>this.canInvite(p)).map(p=>p.email));
    },
    selectedReady(){
      return this.inviteEmails.filter(email=>this.invitableEmails.has(email));
    },
    selectableVisible(){
      return [...new Set(this.selected.filter(p=>this.canInvite(p)).map(p=>p.email))];
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
        this.pairEmails=[];this.inviteEmails=[];this.roleEdits={};
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
    async saveLogin(person){
      const login=(this.loginEdits[person.id]||'').trim();
      if(!login)return;
      if(!await this.run('roster_login',{record_id:person.id,login}))
        this.error=this.view.message||'Could not save native account login.';
      else this.$delete(this.loginEdits,person.id);
    },
    async linkNativeEmail(person){
      const email=(this.nativeEmailEdits[person.id]||'').trim();
      if(!email)return;
      if(!window.confirm('Associate Supervisely login '+
        (person.source_fields?.supervisely_login||'')+' with '+email+
        '? Only continue if this is the account\'s registered email.'))return;
      if(!await this.run('roster_link_native_email',{record_id:person.id,email}))
        this.error=this.view.message||'Could not verify the native identity.';
      else this.$delete(this.nativeEmailEdits,person.id);
    },
    async nativeRole(person){
      const choice=this.roleEdits[person.id]||person.event_role;
      if(choice===person.event_role||person.invitation_status!=='accepted')return;
      const target={monitor:'Manager',participant:'Annotator',
        participant_monitor:'Reviewer'}[choice];
      const team=this.view.event_settings?.team_id||'the event team';
      if(!window.confirm('Change '+person.email+' to '+target+
        ' in Supervisely Team '+team+'? This changes live permissions immediately.'))return;
      if(!await this.run('roster_native_role',{
        record_id:person.id,event_role:choice,confirm_native_role_change:true
      }))this.error=this.view.message||'Native role change failed; inspect membership.';
      else this.$delete(this.roleEdits,person.id);
    },
    async createPair(){
      if(this.disabled||this.busy||this.pairEmails.length!==2||
         !this.pairName.trim()||!this.pairEligible)return;
      if(!window.confirm('Register '+this.pairName.trim()+' with exactly these two accepted accounts?\n\n'+
        this.pairEmails.join('\n')+'\n\nNo new Supervisely invitations will be sent.'))return;
      this.busy=true;this.error='';
      try{
        if(!await this.run('roster_create_pair',{
          team_name:this.pairName.trim(),emails:[...this.pairEmails],
          confirm_eligibility:true
        }))throw new Error(this.view.message||'Could not register the pair.');
        this.pairName='';this.pairEmails=[];this.pairEligible=false;
      }catch(e){this.error=e.message||'Could not register the pair.';}
      finally{this.busy=false;}
    },
    async command(action) {
      if(!await this.run(action))this.error=this.view.message||'Action failed.';
      else this.error='';
    },
    asPairs(){return this.command('roster_prepare_pairs');},
    canInvite(person){
      return Boolean(person.email&&person.invitable&&
        ['not_invited','invitation_error'].includes(person.invitation_status));
    },
    selectVisibleInvitees(){
      // Add only the currently filtered, eligible accounts. Preserve selections
      // from the other tab, never silently add unseen people.
      this.inviteEmails=[...new Set([...this.selectedReady,...this.selectableVisible])];
    },
    clearInviteSelection(){this.inviteEmails=[];},
    async copySelectedForSupervisely(){
      const emails=[...this.selectedReady];
      if(!emails.length){this.error='Select one or more eligible people first.';return;}
      const roleLabels={monitor:'Manager',participant:'Annotator',
        participant_monitor:'Reviewer'};
      const rows=['Email\tSupervisely role',...emails.map(email=>{
        const person=this.profiles.find(p=>p.email===email);
        return email+'\t'+(roleLabels[person?.event_role]||'Needs review');
      })];
      const text=rows.join('\n');
      try{
        if(!navigator.clipboard?.writeText)throw new Error('Clipboard API unavailable');
        await navigator.clipboard.writeText(text);
        this.error='';
      }catch(_){
        // Manual paste fallback for non-secure browser origins; never expose
        // account tokens, only email addresses and approved event roles.
        window.prompt('Copy selected emails and their assigned roles:',text);
      }
    },
    async inviteSelected(){
      if(this.disabled||this.busy)return;
      if(this.invitationAuthBlocked){
        this.error='This session already received HTTP 401 for API invitations. '+
          'Copy selected emails and roles, invite in Supervisely Team Members, then refresh.';
        return;
      }
      const chosen=[...this.inviteEmails];
      if(!chosen.length){this.error='Select one or more people first.';return;}
      if(chosen.some(email=>!this.invitableEmails.has(email))){
        this.error='Selection changed. Review the current roles and invitation statuses.';
        this.inviteEmails=this.selectedReady;
        return;
      }
      const preview=chosen.slice(0,8).join('\n');
      const more=chosen.length>8?'\n…and '+(chosen.length-8)+' more':'';
      if(!window.confirm('Send real Supervisely invitations to exactly '+
        chosen.length+' selected account(s)?\n\n'+preview+more+
        '\n\nRoles will follow the reviewed event roster.'))return;
      this.busy=true;this.error='';
      try{
        this.inviteFailures=[];
        const result=await this.run('registration_invite_selected',{invite_emails:chosen});
        if(!result)
          throw new Error(this.view.message||'Could not send the selected invitations.');
        this.inviteFailures=result?.invitations?.errors||[];
        if(result?.invitations?.authentication_unavailable){
          this.invitationAuthBlocked=true;
          this.error='Supervisely rejected personal API-key authentication (HTTP 401). '+
            'No further invitations were attempted; '+
            (result.invitations.not_attempted||0)+
            ' selected account(s) were not attempted. Copy your selection, '+
            'invite through Supervisely Team Members, then Refresh.';
          this.inviteEmails=this.selectedReady;
        }else if(result?.invitations?.endpoint_unavailable){
          this.error='Supervisely returned 404 Page not found for the invitation endpoint. '+
            'The batch stopped after the first failure; '+
            (result.invitations.not_attempted||0)+
            ' selected account(s) were not attempted. Invite through the native '+
            'Supervisely Team Members page and refresh this roster.';
          this.inviteEmails=this.selectedReady;
        }else{
          if(this.inviteFailures.length){
            this.error=this.inviteFailures.length+
              ' account(s) could not be invited or require inspection. See the exact API errors below.';
          }
          this.inviteEmails=[];
        }
      }catch(e){this.error=e.message||'Could not send selected invitations.';}
      finally{this.busy=false;}
    },
    async refresh(){
      if(this.disabled||this.busy)return;
      this.busy=true;this.error='';
      try {
        const result=await this.run('registration_refresh');
        if(!result)throw new Error(this.view.message||'Could not refresh Supervisely team membership.');
        this.lastSync=result?.membership_sync||null;
        this.inviteFailures=[];
        this.inviteEmails=this.selectedReady;
      }catch(e){this.error=e.message||'Could not refresh team members.';}
      finally{this.busy=false;}
    }
  },
  template:`<section class="nj-panel nj-people-roster">
    <h3>Supervisely team members &amp; event roster</h3>
    <p class="nj-muted">Already invited people directly in Supervisely? Refresh the shared
      team here. New monitors and participants appear in this roster without
      sending another invitation or changing anyone's native role.</p>
    <div class="nj-actions">
      <button class="nj-primary" @click="refresh" :disabled="disabled||busy">
        Refresh participants &amp; monitors from Supervisely
      </button>
      <span class="nj-muted">Last checked: {{view.event_settings?.membership_synced_at?
        new Date(view.event_settings.membership_synced_at*1000).toLocaleString():'Not yet refreshed'}}</span>
    </div>
    <p class="nj-muted">Alternatively, import the official registration workbook with
      Monitors and Participants sheets. Imported source columns and reviewed
      event roles are preserved. Importing does not invite anyone.</p>
    <div class="nj-form-row">
      <label>Event roster (.xlsx)
        <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          @change="fileChanged" :disabled="disabled||busy">
      </label>
      <button class="nj-primary" @click="load" :disabled="disabled||busy||!file">Import both sheets</button>
    </div>
    <p v-if="error" class="nj-message nj-error" role="alert">{{error}}</p>
    <div v-if="inviteFailures.length" class="nj-message nj-error" role="alert">
      <strong>Supervisely invitation errors (no automatic retry):</strong>
      <ul><li v-for="failure in inviteFailures" :key="failure.email">
        <strong>{{failure.email}}</strong> ({{failure.status}}): {{failure.error}}
      </li></ul>
    </div>
    <p v-if="lastSync" class="nj-message" role="status">
      Sync complete: {{lastSync.new}} newly discovered,
      {{lastSync.accepted}} accepted, {{lastSync.pending}} pending,
      {{lastSync.needs_attention}} need review. No invitations were sent.
    </p>
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
    <p v-if="nativeProfiles.length" class="nj-muted">
      {{nativeProfiles.length}} native team account(s) discovered that were not
      already present in the workbook. {{acceptedMembers}} verified accepted
      member record(s) are visible. Entries without a public account email or
      with an unrecognized role require review before pairing.
    </p>
    <div v-if="unpaired.length" class="nj-panel">
      <h4>Register a two-person competition pair</h4>
      <p class="nj-muted">Choose two accepted, unassigned participants discovered
        from Supervisely or imported from the official roster. The app never
        guesses partners from team membership.</p>
      <label>Pair name<input v-model.trim="pairName" maxlength="180"
        placeholder="Team name" :disabled="disabled||busy"></label>
      <div class="nj-check-group">
        <label v-for="p in unpaired" :key="p.email" class="nj-check">
          <input type="checkbox" :value="p.email" v-model="pairEmails"
            :disabled="disabled||busy||(!pairEmails.includes(p.email)&&pairEmails.length>=2)">
          {{p.display_name||p.email}} ({{p.email}})
        </label>
      </div>
      <label class="nj-check"><input type="checkbox" v-model="pairEligible"
        :disabled="disabled||busy">I verified both registrations and required
        participant permissions/consent for this event.</label>
      <button class="nj-primary" @click="createPair"
        :disabled="disabled||busy||pairEmails.length!==2||!pairName.trim()||!pairEligible">
        Register selected pair
      </button>
    </div>
    <div v-if="participants.length" class="nj-panel">
      <h4>Prepare eligible pairs from the registration workbook</h4>
      <p class="nj-muted">Eligible complete pairs: {{completeTeams}}.
        Incomplete teams, cancelled registrations, missing emails, records with
        missing processing consent or conflicting roles are retained for review.
        Preparing pairs never sends an invitation.</p>
      <button @click="asPairs" :disabled="disabled||busy||!completeTeams">
        Prepare complete eligible pairs
      </button>
    </div>
    <p class="nj-caution">Invitations are sent only to the accounts you select.
      Selected accounts can span both sheets. Existing pending or accepted invitations
      cannot be resent. Real Supervisely permissions come from the reviewed roster.
      On app.supervisely.com, the website's invitation endpoint may reject personal API
      tokens even for a Team Admin. In that case, copy your selection, invite on
      <a href="https://app.supervisely.com/members/add" target="_blank" rel="noopener noreferrer">Supervisely Team Members</a>,
      and refresh this roster. Do not share browser session credentials.</p>
    <div class="nj-form-row">
      <label>Sheet<select v-model="group">
        <option value="Monitors">Monitors</option>
        <option value="Participants">Participants</option></select></label>
      <label>Find name, email or team<input v-model="search" placeholder="Filter records"></label>
    </div>
    <div class="nj-actions">
       <button @click="selectVisibleInvitees" :disabled="disabled||busy||!selectableVisible.length">
        Select matching eligible ({{selectableVisible.length}})
      </button>
      <button @click="clearInviteSelection" :disabled="disabled||busy||!inviteEmails.length">
        Clear selection
      </button>
      <button @click="copySelectedForSupervisely" :disabled="disabled||busy||!selectedReady.length">
        Copy selected emails &amp; roles
      </button>
      <button class="nj-primary" @click="inviteSelected"
        :disabled="disabled||busy||!selectedReady.length||invitationAuthBlocked">
        Invite selected ({{selectedReady.length}})
      </button>
    </div>
    <div class="nj-table-scroll"><table class="nj-table">
      <thead><tr><th>Select</th><th>Team</th><th>Name / email</th><th>Event role</th>
        <th>Invitation</th><th>Review</th><th>Fields</th></tr></thead>
      <tbody><tr v-for="p in selected" :key="p.id">
        <td><input type="checkbox" :value="p.email" v-model="inviteEmails"
          :disabled="disabled||busy||!canInvite(p)"
          :title="canInvite(p)?'Select for invitation':'Not eligible, already invited or awaiting review'"
          :aria-label="'Select '+(p.display_name||p.email)+' for invitation'"></td>
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
          <button v-if="p.invitation_status!=='accepted'" @click="role(p)"
            :disabled="disabled||busy||!roleEdits[p.id]||
              roleEdits[p.id]===p.event_role">Save</button>
          <button v-else @click="nativeRole(p)" :disabled="disabled||busy||
              !roleEdits[p.id]||roleEdits[p.id]===p.event_role">
            Change live role</button>
        </div></td>
        <td><strong>{{p.invitation_status}}</strong>
          <small v-if="p.native_login" class="nj-small-text">
            Native login: {{p.native_login}}</small>
          <small v-if="p.native_role" class="nj-small-text">
            Native role: {{p.native_role}}</small>
          <p v-if="p.invitation_error" class="nj-caution" role="alert">
            {{p.invitation_error}}</p>
        </td>
        <td><span v-if="p.needs_review" class="nj-badge warning">Review</span>
          <span v-else>Ready to evaluate</span></td>
        <td><details><summary>Source columns &amp; Supervisely login</summary>
          <p v-if="!p.email && p.source_fields?.imported_via==='supervisely'"
             class="nj-muted">Supervisely did not disclose this account's email.
             Link its registered address to use this person for event registration.</p>
          <div v-if="!p.email && p.source_fields?.imported_via==='supervisely'"
            class="nj-inline-actions">
            <input v-model.trim="nativeEmailEdits[p.id]" type="email"
              placeholder="Registered account email" :disabled="disabled||busy">
            <button @click="linkNativeEmail(p)"
              :disabled="disabled||busy||!nativeEmailEdits[p.id]">
              Link native account
            </button>
          </div>
          <p class="nj-muted">Set the exact native login when it differs from email.
            Existing invited account identities require manual review.</p>
          <div class="nj-inline-actions">
            <input :value="loginEdits[p.id]===undefined?
              (p.source_fields?.supervisely_login||''):loginEdits[p.id]"
              @input="$set(loginEdits,p.id,$event.target.value)"
              maxlength="180" placeholder="Supervisely login" :disabled="disabled||busy">
            <button @click="saveLogin(p)" :disabled="disabled||busy||
              !(loginEdits[p.id]||'').trim()">Save login</button>
          </div>
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
