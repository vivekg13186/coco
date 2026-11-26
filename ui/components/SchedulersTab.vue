<template>
  <div class="tab-root">
    <Toolbar class="tab-toolbar">
      <template #start>
        <Button label="New schedule" icon="pi pi-plus" size="small" disabled />
      </template>
    </Toolbar>
    <div class="panel-pad">
      <Message severity="info" icon="pi pi-calendar-clock">
        Schedules arrive with the scheduler (milestone 4). This tab will list each schedule with its cron
        expression in plain English, next run, last status and an enabled switch; Edit will open a schedule
        editor tab here.
      </Message>
    </div>
  </div>
</template>

<style scoped>
.tab-root {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.tab-toolbar {
  border: 0;
  border-bottom: 1px solid var(--p-content-border-color);
  border-radius: 0;
  padding: 0.5rem 1rem;
}
</style>
