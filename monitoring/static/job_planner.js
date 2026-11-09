/* Pre-event logical job allocation: overlap, complete videos and job count.
 * These are durable app-level jobs; native Supervisely activation is separate.
 */
Vue.component('event-job-planner', {
  props:['view','run','disabled'],
  data() {return {
    selectedDatasetIds:[],selectedRegistrationIds:[],
    jobsPerTeam:3,videosPerTeam:0,imageReplicas:1,videoReplicas:1,
    imageCommonPercent:0,imageEffort:1,seed:2026,
    previewInputs:'',localError:''
  };},
  computed:{
    sources() {return this.view.sources?.datasets||[];},
    pairs() {return this.view.registration_rows||[];},
    prepared() {return this.view.prepared_jobs||[];},
    plan() {return this.view.job_preview;},
    planId() {return this.view.job_plan_id;},
    maxReplicas() {return Math.max(1,this.selectedRegistrationIds.length);},
    ready() {return !!this.view.event_settings?.single_team&&!!this.view.can_setup;},
    settingsFingerprint(){
      return JSON.stringify({
        d:this.selectedDatasetIds.slice().sort(),
        r:this.selectedRegistrationIds.slice().sort(),
        j:this.jobsPerTeam,v:this.videosPerTeam,ir:this.imageReplicas,
        vr:this.videoReplicas,cp:this.imageCommonPercent,ie:this.imageEffort,s:this.seed
      });
    },
    unchanged() {return !!this.previewInputs&&this.previewInputs===this.settingsFingerprint;}
  },
  methods:{
    config() {return {
      jobs_per_team:Number(this.jobsPerTeam),
      videos_per_team:Number(this.videosPerTeam),
      image_replicas:Number(this.imageReplicas),
      video_replicas:Number(this.videoReplicas),
      image_common_percent:Number(this.imageCommonPercent),
      image_effort:Number(this.imageEffort),
      seed:Number(this.seed)
    };},
    async preview() {
      this.localError='';
      const signature=this.settingsFingerprint;
      const ok=await this.run('job_preview',{
        dataset_ids:this.selectedDatasetIds,
        registration_ids:this.selectedRegistrationIds,
        image_effort:Number(this.imageEffort),
        job_config:this.config()
      });
      if(ok)this.previewInputs=signature;
      return ok;
    },
    async apply() {
      if(!this.unchanged) {this.localError='Job settings changed. Preview again.';return;}
      this.localError='';
      const ok=await this.run('job_apply',{job_plan_id:this.planId});
      if(ok)this.previewInputs='';
    },
    pairName(id) {return this.pairs.find(t=>t.id===id)?.team_name||id;}
  },
  template:`<section class="nj-panel nj-job-planner">
    <h3>2. Distribute whole videos to teams</h3>
    <p>Choose the video datasets and teams, set the number of jobs, then review the distribution. A video is never split.</p>
    <p v-if="!ready" class="nj-caution">Configure the event's existing Supervisely team first.</p>
    <div class="nj-columns">
      <div>
        <h4>Source video datasets</h4>
        <button v-if="sources.length" @click="selectedDatasetIds=sources.filter(s=>s.kind==='video').map(s=>s.id)" :disabled="disabled||!ready">Select all videos</button>
        <p v-if="!sources.length" class="nj-muted">Upload a video dataset above, then reload the lists.</p>
        <label v-for="s in sources" :key="s.id" class="nj-check">
          <input type="checkbox" :value="s.id" v-model="selectedDatasetIds" :disabled="disabled||!ready||s.kind!=='video'">
          {{s.project}} / {{s.name}} · {{s.kind}} · {{s.items}} files
        </label>
      </div>
      <div>
        <h4>Competition teams</h4>
        <button v-if="pairs.length" @click="selectedRegistrationIds=pairs.map(p=>p.id)" :disabled="disabled||!ready">Select all teams</button>
        <p v-if="!pairs.length" class="nj-muted">Add teams in “2. Add teams” before preparing jobs.</p>
        <label v-for="p in pairs" :key="p.id" class="nj-check">
          <input type="checkbox" :value="p.id" v-model="selectedRegistrationIds" :disabled="disabled||!ready">
          {{p.team_name}} · {{p.membership_status==='ready'?'Accepted':'Waiting for native email acceptance'}}
        </label>
      </div>
    </div>
    <div class="nj-form-row">
      <label>Jobs per team
        <input type="number" v-model.number="jobsPerTeam" min="1" max="100" step="1" :disabled="disabled">
      </label>
      <label>Complete videos per team
        <input type="number" v-model.number="videosPerTeam" min="0" max="100" step="1" :disabled="disabled">
      </label>
    </div>
    <details class="nj-advanced"><summary>Advanced: video overlap and workload balancing</summary>
    <div class="nj-form-row">
      <label>Video overlap (max teams per video)
        <input type="number" v-model.number="videoReplicas" min="1" :max="maxReplicas" step="1" :disabled="disabled">
      </label>
      
      <label>Reproducible allocation seed
        <input type="number" v-model.number="seed" min="0" step="1" :disabled="disabled">
      </label>
    </div>
    </details>
    <p class="nj-muted">Videos are never split across teams or jobs. A complete video counts as
      one indivisible media item, weighted by its frame count. Team workloads
      and all planned jobs are displayed before confirmation.</p>
    <p v-if="localError" class="nj-message nj-error">{{localError}}</p>
    <p v-if="prepared.length" class="nj-message">Jobs are prepared. Use “Reset dataset preparation” above to change the distribution and prepare them again.</p>
    <button @click="preview" class="nj-primary"
      :disabled="disabled||!ready||!selectedDatasetIds.length||!selectedRegistrationIds.length||!!prepared.length">
      Preview distribution and jobs
    </button>
    <div v-if="plan && planId" class="nj-panel">
      <h4>Job preparation preview</h4>
      <p>{{plan.total_jobs}} logical jobs across {{plan.team_count}} competition teams ·
        {{plan.source_videos}} whole videos ·
        effort spread {{plan.workload_spread}}</p>
      <div class="nj-table-scroll"><table class="nj-table">
        <thead><tr><th>Team</th><th>Complete videos</th>
          <th>Jobs</th><th>Workload by job</th></tr></thead>
        <tbody><tr v-for="t in plan.teams" :key="t.registration_id">
          <td>{{pairName(t.registration_id)}}</td><td>{{t.videos}}</td><td>{{t.jobs}}</td>
          <td>{{t.job_efforts.join(' / ')}}</td>
        </tr></tbody>
      </table></div>
      <p class="nj-muted">No native labeling jobs have been published yet.
        This step saves the complete immutable app-level job plan.</p>
      <button @click="apply" class="nj-primary" :disabled="disabled||!unchanged">
        Confirm and prepare {{plan.total_jobs}} logical jobs
      </button>
    </div>
    <div v-if="prepared.length" class="nj-panel">
      <h4>Prepared jobs ({{prepared.length}})</h4>
      <div class="nj-table-scroll"><table class="nj-table">
        <thead><tr><th>Team</th><th>Job</th><th>Videos</th>
          <th>Effort</th><th>State</th></tr></thead>
        <tbody><tr v-for="job in prepared" :key="job.id">
          <td>{{job.team_name}}</td><td>{{job.position}}</td><td>{{job.videos}}</td><td>{{job.effort}}</td><td>{{job.state}}</td>
        </tr></tbody>
      </table></div>
      <p class="nj-muted">Native jobs and sequential release will be enabled
        only after the shared-team assignment and return-for-correction
        integration passes real Supervisely account validation.</p>
    </div>
  </section>`
});
