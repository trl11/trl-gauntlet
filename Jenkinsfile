pipeline {
    // Every Jenkins worker has the `docker` label. Build the project-specific
    // CI image there so this release job can use any available worker while
    // retaining a reproducible toolchain.
    agent {
        dockerfile {
            filename 'ci/Dockerfile'
            label 'docker'
            additionalBuildArgs '--build-arg APT_PROXY_URL=http://192.168.11.112:3142'
            args '''
                --group-add 999
                -v /var/run/docker.sock:/var/run/docker.sock
                -v /usr/bin/docker:/usr/bin/docker
                -v /usr/libexec/docker:/usr/libexec/docker
            '''
        }
    }

    options {
        ansiColor('xterm')
        timeout(time: 180, unit: 'MINUTES')
    }

    environment {
        CI = 'true'
        HOME = "${WORKSPACE}"
    }

    stages {
        stage('Setup') {
            steps {
                sshagent(credentials: ['github-ssh']) {
                    sh 'git submodule update --init --recursive'
                }
                sh 'make setup'
            }
        }

        stage('Check') {
            steps {
                sh 'make check'
            }
        }

        stage('Build release artifacts') {
            steps {
                sh 'make build'
                sh 'make ci-validate-dist'
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'dist/*', allowEmptyArchive: true, fingerprint: true
            junit allowEmptyResults: true, testResults: 'build/junit.xml'
            cleanWs(deleteDirs: true, patterns: [[pattern: '.git/**', type: 'EXCLUDE']])
        }
        regression {
            slackSend(
                color: 'danger',
                message: ":red_circle: *${env.JOB_NAME}* #${env.BUILD_NUMBER} - FAILURE\n<${env.BUILD_URL}|View Build>",
                notifyCommitters: true
            )
        }
        fixed {
            slackSend(
                color: 'good',
                message: ":large_green_circle: *${env.JOB_NAME}* #${env.BUILD_NUMBER} - FIXED\n<${env.BUILD_URL}|View Build>",
                notifyCommitters: true
            )
        }
    }
}
