I want to design a simple workflow engine for solo developer.
Workflow to be defined in custom programming language closely related to python,snippet give below.
- Core a library to take the code and run the workflow
Takes object input + return object output + event for each step 
- Engine + REST API ,to perform CURD operations ,store workflow definitions, and execute the workflow use sqllite as backend.

- UI use https://primevue.dev/nuxt 
- UI layout
Tabbed WorkPanel 
  - Non-closable panel
  - Workflow Overview Panel - on edit open new window
  - Run History Overview Panel - on edit open new window
  - Scheduler Overview Panel - on edit open new window

Language Syntax
```coco
# comments
# primitive string,boolean,int,float,object,array
package maind
import asdsd

class Name:
	int a
	string b
	Person a[]

step step1(int a,int b,string c)(int a):
	expression like python

step step2(int a,int b,string c)(int a):
	expression like python

flow flow1(int a,int b,string c)(int a):
	int d,f
	(d,f) = step1(a,b)
	if d > 10:
		for i in range(10,m,1):
			step2(asd)
```
Lang can import python packages  and call python function in steps 
flow only can do step call if and for



  