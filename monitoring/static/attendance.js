/* Logical competition teams and event check-in; all changes are server-authorized. */
Vue.component('event-roster-assignment', {
  props: ['view','run','disabled'],
  data() { return { selectedMonitors:[], rebalance:true, manual:{}, initialized:false }; },
  computed: {
    monitors() { return this.view.attendance?.monitors || []; },
    teams() { return this.view.registration_rows || []; },
    eligible() { return this.teams.filter(t=>(t.members||[]).length===2 && !t.team_id); },
    plan() { return this.view.roster_balance_preview; },
    planId() { return this.view.roster_plan_id; },
    names() { return Object.fromEntries(this.monitors.map(m=>[m.id,m.name||m.login])); },
    counts() {
      const counts=Object.fromEntries(this.monitors.map(m=>[m.id,0]));
      for(const t of this.teams) if(Object.prototype.hasOwnProperty.call(counts,t.monitor_id)) counts[t.monitor_id]++;
      return counts;
    }
  },
  watch: {monitors:{immediate:true,handler(m){
    if(!this.initialized && m.length){this.selectedMonitors=m.map(x=>x.id);this.initialized=true;}
  }}},
  methods: {
    preview() {return this.run('roster_balance_preview',{monitor_ids:this.selectedMonitors,rebalance:this.rebalance});},
    apply() {return this.run('roster_balance_apply',{roster_plan_id:this.planId});},
    async assign(t) {
      const mid=Number(this.manual[t.id]);if(!mid)return;
      const ok=await this.run('roster_assign',{registration_id:t.id,monitor_id:mid});
      if(ok)this.$delete(this.manual,t.id);
    }
  },
  template: `<section class="nj-panel nj-event-roster">
    <h3>Competition teams and monitor allocation</h3>
    <p class="nj-muted">Competition pairs are separate logical entities in the monitoring app.
      This roster is ready for the planned single shared Supervisely team migration;
      the existing native provisioning flow has not yet been migrated.</p>
    <div class="nj-metrics nj-three-metrics">
      <div><span>Registered pairs</span><strong>{{teams.length}}</strong></div>
      <div><span>Not provisioned</span><strong>{{eligible.length}}</strong></div>
      <div><span>Unassigned pairs</span><strong>{{teams.filter(t=>!t.monitor_id).length}}</strong></div>
    </div>
    <div v-if="monitors.length">
      <h4>Event monitors and current workloads</h4>
      <div class="nj-monitor-loads">
        <div v-for="m in monitors" :key="m.id" class="nj-load-item">
          <strong>{{m.name}}</strong><span>{{counts[m.id] || 0}} teams</span>
        </div>
      </div>
      <p class="nj-muted">Choose monitors for balanced distribution. The preview preserves
        provisioned teams and can rebalance pairs that have not been provisioned.
        Apply performs no native Supervisely job operations.</p>
      <div class="nj-check-group">
        <label v-for="m in monitors" :key="m.id" class="nj-check">
          <input type="checkbox" v-model="selectedMonitors" :value="m.id">{{m.name}}
        </label>
      </div>
      <label class="nj-check"><input type="checkbox" v-model="rebalance">
        Rebalance previously assigned but not-yet-provisioned pairs
      </label>
      <button class="nj-primary" @click="preview" :disabled="disabled || !selectedMonitors.length || !eligible.length">
        Preview balanced assignments
      </button>
      <div v-if="plan" class="nj-panel">
        <h4>Review proposed assignments</h4>
        <p v-for="issue in plan.issues || []" :key="issue" class="nj-caution">{{issue}}</p>
        <div class="nj-monitor-loads">
          <div v-for="(count,mid) in plan.loads || {}" :key="mid" class="nj-load-item">
            {{names[mid] || 'Monitor '+mid}}: {{count}} teams
          </div>
        </div>
        <p v-for="item in plan.rows || []" :key="item.registration_id">
          {{item.team_name}}: {{names[item.from_monitor_id] || 'Unassigned'}} → {{names[item.monitor_id]}}
        </p>
        <button v-if="planId" class="nj-primary" @click="apply" :disabled="disabled">
          Apply {{plan.rows.length}} assignments
        </button>
      </div>
    </div>
    <p v-else>No event monitors registered yet.</p>
    <h4>Registered competition pairs</h4>
    <div class="nj-table-scroll"><table class="nj-table">
      <thead><tr><th>Team</th><th>First participant</th><th>Second participant</th>
        <th>Monitor</th><th>Assignment</th></tr></thead>
      <tbody><tr v-for="t in teams" :key="t.id">
        <td><strong>{{t.team_name}}</strong></td>
        <td>{{(t.members||[])[0]?.name || (t.members||[])[0]?.email || 'Missing'}}</td>
        <td>{{(t.members||[])[1]?.name || (t.members||[])[1]?.email || 'Missing'}}</td>
        <td>{{names[t.monitor_id] || (t.monitor_id?'Monitor '+t.monitor_id:'Unassigned')}}</td>
        <td>
          <span v-if="t.team_id" class="nj-muted">Already provisioned — use native handover</span>
          <span v-else-if="(t.members||[]).length!==2" class="nj-caution">Two members required</span>
          <div v-else class="nj-inline-actions">
            <select :disabled="disabled" :aria-label="'Monitor for '+t.team_name"
              :value="manual[t.id] || t.monitor_id || 0"
              @change="$set(manual,t.id,Number($event.target.value))">
              <option :value="0" disabled>Choose monitor</option>
              <option v-for="m in monitors" :key="m.id" :value="m.id">{{m.name}}</option>
            </select>
            <button @click="assign(t)" :disabled="disabled || !manual[t.id] || Number(manual[t.id])===t.monitor_id">Save</button>
          </div>
        </td>
      </tr></tbody>
    </table></div>
    <p v-if="!teams.length" class="nj-muted">Import registration data to populate competition teams and members.</p>
    <p class="nj-muted">Native reviewer handover remains a separate, permission-checked operation.
      DEMO monitoring privileges must be enabled in the actual Supervisely account.</p>
  </section>`
});

Vue.component('event-attendance', {
  props: ['view','form','post','locked'],
  data() {return {busy:false,query:'',status:'all',notes:{},tables:{},clientError:'',timer:null};},
  computed: {
    attendance() {return this.view.attendance || {teams:[],summary:{}};},
    summary() {return this.attendance.summary || {};},
    teams() {
      const q=this.query.trim().toLowerCase();
      return (this.attendance.teams||[]).filter(t=>
        (this.status==='all'||t.arrival_status===this.status) &&
        (!q||[t.name,t.monitor_name,...(t.members||[]).flatMap(p=>[p.name,p.email])]
          .some(s=>String(s||'').toLowerCase().includes(q))));
    }
  },
  methods: {
    key(t,p) {return t.id+'/'+p.email;},
    date(ts) {return ts?new Date(ts*1000).toLocaleString():'';},
    async setTable(team) {
      if(this.busy||this.locked||!team.can_set_table)return;
      this.busy=true;this.clientError='';
      Object.assign(this.form,{logistics_action:'set_table',registration_id:team.id,
        table_number:String(this.tables[team.id]??team.table_number??'').trim()});
      try {
        const result=await this.post('/logistics/action');
        if(result?.ok===false||this.view.error)throw new Error(this.view.message||'Could not save table.');
        this.$delete(this.tables,team.id);
      }catch(e){this.clientError=e.message||'Table assignment failed.';}
      finally{this.busy=false;}
    },
    async action(type,team,person) {
      if(this.busy||this.locked)return;
      this.busy=true;this.clientError='';
      const key=team&&person?this.key(team,person):null;
      Object.assign(this.form,{
        attendance_action:type,
        registration_id:team?.id||'',
        member_email:person?.email||'',
        checkin_note:key?(this.notes[key]||''):''
      });
      try {
        const result=await this.post('/attendance/action');
        if(result?.ok===false||this.view.error)throw new Error(this.view.message||'Check-in request failed.');
        if(key)this.$delete(this.notes,key);
      }catch(e){this.clientError=e.message||'Check-in failed.';}
      finally{this.busy=false;}
    }
  },
  mounted() {
    this.action('refresh');
    this.timer=setInterval(()=>{if(!document.hidden&&!this.busy)this.action('refresh');},60000);
  },
  beforeDestroy() {clearInterval(this.timer);},
  template: `<section class="nj-management nj-arrivals">
    <div class="nj-section-heading"><div>
      <h2>Participant check-in</h2>
      <p class="nj-muted">Record each participant's physical arrival. This does not imply
        Supervisely invitation acceptance or completion of an annotation job.</p>
      </div><button @click="action('refresh')" :disabled="busy||locked">Refresh arrivals</button></div>
    <div v-if="clientError" class="nj-message nj-error" role="alert">{{clientError}}</div>
    <div class="nj-metrics">
      <div><span>Participants</span><strong>{{summary.participants||0}}</strong></div>
      <div><span>Checked in</span><strong>{{summary.checked_in||0}}</strong></div>
      <div><span>Missing</span><strong>{{summary.missing||0}}</strong></div>
      <div><span>Complete teams</span><strong>{{summary.complete_teams||0}} / {{summary.teams||0}}</strong></div>
    </div>
    <div class="nj-panel">
      <div class="nj-form-row">
        <label>Search name, email or team<input v-model="query" placeholder="Find participant"></label>
        <label>Status<select v-model="status">
          <option value="all">All teams</option>
          <option value="not_arrived">Not arrived</option>
          <option value="partial">Partially arrived</option>
          <option value="complete">Fully arrived</option>
        </select></label>
      </div>
      <div class="nj-table-scroll"><table class="nj-table">
        <thead><tr><th>Competition team</th><th>Table number</th><th>Participant</th><th>Monitor</th><th>Arrival</th><th>Action</th></tr></thead>
        <tbody>
          <template v-for="team in teams">
            <tr v-for="person in team.members" :key="team.id+'-'+person.email">
              <td><strong>{{team.name}}</strong><small class="nj-small-text">{{team.present}} / {{team.total}} present</small></td>
              <td><div class="nj-inline-actions"><input type="number" min="1" max="9999"
                :aria-label="'Table number for '+team.name"
                :value="tables[team.id]===undefined?team.table_number:tables[team.id]"
                @input="$set(tables,team.id,$event.target.value)"
                :disabled="busy||locked||!team.can_set_table" placeholder="Table">
                <button @click="setTable(team)" :disabled="busy||locked||!team.can_set_table||
                  tables[team.id]===undefined||String(tables[team.id])===String(team.table_number)">Save</button></div></td>
              <td>{{person.name}}<small class="nj-small-text">{{person.email}}</small></td>
              <td>{{team.monitor_name}}</td>
              <td><span class="nj-badge" :class="{warning:!person.checked_in}">
                {{person.checked_in?'Present':'Not arrived'}}</span>
                <small v-if="person.checked_in" class="nj-small-text">{{date(person.checked_in_at)}}</small></td>
              <td><button v-if="!person.checked_in" class="nj-primary"
                @click="action('checkin',team,person)" :disabled="busy||locked">Check in</button>
                <div v-else class="nj-inline-actions">
                  <input :aria-label="'Undo reason for '+person.name"
                    :value="notes[key(team,person)]||''"
                    @input="$set(notes,key(team,person),$event.target.value)"
                    maxlength="500" placeholder="Reason to undo">
                  <button @click="action('undo',team,person)"
                    :disabled="busy||locked||!(notes[key(team,person)]||'').trim()">Undo</button>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table></div>
      <p v-if="!teams.length" class="nj-muted">No matching participants, or no teams assigned to your account.</p>
      <p class="nj-muted">Each action is audited with actor and time. Reversals require a reason.
        Team status becomes fully arrived only when both registered members are checked in.</p>
    </div>
  </section>`
});
